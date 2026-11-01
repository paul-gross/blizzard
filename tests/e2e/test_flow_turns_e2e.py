"""The hub's flow driver takes every hub step as a turn on the flows of the branches it lands on, against the mock forge.

Flows on different repositories run side by side; one branch's chunks land in arrival order; a pending turn yields its
branch until its next poll; a visit holding no branch (a garden delivery, ``record-findings``) never waits on one; a
paused chunk takes no turn. Each scenario ends with the flow invariants holding and no turn open.
Needs ``BLIZZARD_E2E=1`` and ``blizzard-mock``."""

from __future__ import annotations

import itertools
import os
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest
import yaml

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.graphs import PACKAGED
from blizzard.runner.config import RunnerConfig
from blizzard.tools.invariants import hub_invariants
from tests.e2e.flow_support import flow_runner_config, flow_world, pinned_chunk, set_lever, ticking_runner
from tests.fleet_launchers import OWNER, REPO, run_forge, run_hub
from tests.fleet_world import REPO_NAME
from tests.support import create_repositories, fixture_repository, free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e flow turns need the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

WEB_REPO_NAME = "toy-web"
WEB_REPO = f"{OWNER}/{WEB_REPO_NAME}"
_FLOW_INVARIANTS = frozenset({"hub:one-turn-per-flow", "hub:one-turn-per-visit", "hub:flow-line-standing"})
#: Comfortably past the driver's two-second tick, so a converged line is never a race.
_DRIVER_WAIT = 60.0


_SCOPE_FOUND = "garden-found"
_UNKNOWN_FINDING_ID = "fin_0" + "Z" * 25


def _common() -> str:
    return (
        "import json, os, pathlib, subprocess\n"
        "chunk_id = os.environ['BLIZZARD_CHUNK_ID']\n"
        "def sh(*args, inp=None):\n"
        "    return subprocess.run(list(args), input=inp, check=True, capture_output=True, text=True).stdout\n"
        "item = json.loads(sh('blizzard', 'runner', 'work-items', chunk_id))['items'][0]\n"
        "lines = item['body'].splitlines()\n"
        "path = next(l.split('=', 1)[1] for l in lines if l.startswith('path='))\n"
        "scope = next(l for l in lines if l.startswith('Scope:')).split()[1]\n"
        "def publish(name, content):\n"
        "    sh('blizzard', 'runner', 'artifact', 'create', '--name', name, inp=content)\n"
        "def rows():\n"
        "    return json.loads(sh('blizzard', 'runner', 'garden', 'findings'))\n"
        "def bucket():\n"
        "    # ops and matches target only rows under this run's own scope\n"
        "    return [r for r in rows() if r['scope_slug'] == scope and r['live']]\n"
        "addendum_marker = pathlib.Path(f'.garden-addendum-{chunk_id}')\n"
    )


_SURVEY = (
    "rev = sh('git', '-C', 'toy-api', 'rev-parse', 'HEAD').strip()\n"
    "fmt = sh('blizzard', 'runner', 'artifact', 'get', '--scope', 'system', 'garden/finding-format', '--content')\n"
    "assert 'FindingDelta' in fmt, 'system-scope finding format did not resolve'\n"
    "if path in ('found', 'invalid', 'virgin'):\n"
    "    cands = [\n"
    "        {'ref': 'F1', 'class': 'stale-docstring', 'locus': 'src/app.py:1', 'summary': 'first weed'},\n"
    "        {'ref': 'F2', 'class': 'stale-docstring', 'locus': 'src/app.py:2', 'summary': 'second weed'},\n"
    "    ]\n"
    "elif path == 'excessive':\n"
    "    cands = [{'ref': 'F1', 'class': 'excessive-scope', 'locus': scope,\n"
    "              'summary': 'roughly four hundred instances across the scope'}]\n"
    "elif path == 'no-strategy':\n"
    "    # As `survey.md` directs when the registry declares no entry for the axis: one\n"
    "    # bail-out candidate naming the registry and the axis, nothing else.\n"
    "    cands = [{'ref': 'F1', 'class': 'undeclared-axis', 'locus': 'gardening-axes registry',\n"
    "              'summary': 'the routine axis has no declared registry entry'}]\n"
    "else:\n"
    "    cands = []\n"
    "measurement = f'e2e sweep of {scope}: {len(cands)} flagged'\n"
    "publish('survey', json.dumps({'scope': scope, 'revisions': {'toy-api': rev},\n"
    "                              'measurement': measurement, 'candidates': cands}))\n"
    "# The skeleton every sweep publishes; reconcile's assembled delta supersedes it on\n"
    "# every path where reconcile runs, and on `clean` it is the delta delivered.\n"
    "publish('delta', json.dumps({'scope': scope, 'revisions': {'toy-api': rev},\n"
    "                             'measurement': measurement, 'findings': []}))\n"
)
_SURVEY_JUDGEMENT = (
    "choice = {'found': 'found', 'invalid': 'found', 'virgin': 'found', 'excessive': 'excessive',\n"
    "          'no-strategy': 'no-strategy', 'clean': 'clean'}[path]\n"
    "verdict(choice, 'scripted sweep')\n"
)
_RECONCILE = (
    "envelope = json.loads(sh('blizzard', 'runner', 'artifact', 'get', 'survey', '--content'))\n"
    "live = bucket()\n"
    "if path == 'virgin':\n"
    "    # the bucket is routine-wide: `found`'s findings are shown under `found`'s own scope\n"
    f"    assert any(r['scope_slug'] == {_SCOPE_FOUND!r} for r in rows()), 'neighbour findings not shown'\n"
    "if path == 'invalid' and not addendum_marker.exists():\n"
    "    delta = {'scope': scope, 'revisions': envelope['revisions'], 'measurement': envelope['measurement'],\n"
    f"             'findings': [{{'op': 'observed', 'id': {_UNKNOWN_FINDING_ID!r}}}]}}\n"
    "else:\n"
    "    ops = []\n"
    "    for cand in envelope['candidates']:\n"
    "        match = next((f for f in live if f['class'] == cand['class'] and f['locus'] == cand['locus']), None)\n"
    "        if match:\n"
    "            ops.append({'op': 'observed', 'id': match['finding_id']})\n"
    "        else:\n"
    "            # `ref` carried through from the candidate: this addition's own\n"
    "            # submission-local name, citable by propose in this same delivery.\n"
    "            ops.append({'op': 'add', 'class': cand['class'], 'locus': cand['locus'],\n"
    "                        'summary': cand['summary'], 'ref': cand['ref']})\n"
    "    delta = {'scope': scope, 'revisions': envelope['revisions'], 'measurement': envelope['measurement'],\n"
    "             'findings': ops}\n"
    "publish('delta', json.dumps(delta))\n"
)
_RECONCILE_JUDGEMENT = "verdict('nothing-to-propose' if addendum_marker.exists() else 'converged', 'scripted match')\n"
_RECONCILE_FROM_DELIVER = (
    "failure = sh('blizzard', 'runner', 'artifact', 'get', 'garden-delivery-failure', '--content')\n"
    "assert 'fin_' in failure, f'failure artifact does not name the rejected id: {failure!r}'\n"
    "good = {'scope': scope, 'revisions': envelope['revisions'], 'measurement': envelope['measurement'],\n"
    "        'findings': [{'op': 'observed', 'id': f['finding_id']} for f in live]}\n"
    "publish('delta', json.dumps(good))\n"
    "addendum_marker.write_text('threaded')\n"
)
_PROPOSE = (
    "fmt = sh('blizzard', 'runner', 'artifact', 'get', '--scope', 'system', 'garden/proposal-format', '--content')\n"
    "assert 'GardenProposalCandidate' in fmt, 'system-scope proposal format did not resolve'\n"
    "live = bucket()\n"
    "if path == 'virgin':\n"
    "    delta = json.loads(\n"
    "        sh('blizzard', 'runner', 'artifact', 'get', 'delta', '--node', 'reconcile', '--content'))\n"
    "    own_refs = [op['ref'] for op in delta['findings'] if op['op'] == 'add']\n"
    "    docket = [{'ref': 'P1', 'class': 'handoff', 'title': f'hand {scope} out of the fleet',\n"
    "               'body': 'scripted proposal citing the refs this very run minted',\n"
    "               'findings': own_refs}]\n"
    "elif path == 'invalid' or not live:\n"
    "    docket = []\n"
    "else:\n"
    "    docket = [{'ref': 'P1', 'class': 'handoff', 'title': f'hand {scope} out of the fleet',\n"
    "               'body': 'scripted proposal citing every live finding in the bucket',\n"
    "               'findings': [f['finding_id'] for f in live]}]\n"
    "publish('docket', json.dumps(docket))\n"
)
_PROPOSE_JUDGEMENT = (
    "verdict('proposed' if (path == 'virgin' or (path != 'invalid' and bucket())) else 'none',\n"
    "        'scripted propose')\n"
)


def _scripted_garden_graph_yaml() -> str:
    """Keep the packaged graph intact while replacing its prompts with scripts."""
    body = PACKAGED.named("garden-routine").body
    common = _common()
    nodes: Any = body["nodes"]
    nodes["survey"]["prompt"] = common + _SURVEY
    nodes["survey"]["judgement"]["prompt"] = common + _SURVEY_JUDGEMENT
    nodes["reconcile"]["prompt"] = common + _RECONCILE
    nodes["reconcile"]["judgement"]["prompt"] = common + _RECONCILE_JUDGEMENT
    nodes["propose"]["prompt"] = common + _PROPOSE
    nodes["propose"]["judgement"]["prompt"] = common + _PROPOSE_JUDGEMENT
    nodes["deliver"]["judgement"]["choices"]["invalid"]["prompt_addendum"] = _RECONCILE_FROM_DELIVER
    return yaml.safe_dump(body, sort_keys=False)


def chunk_detail(hub: httpx2.Client, chunk_id: str) -> dict[str, Any]:
    return hub.get(f"/api/chunks/{chunk_id}").json()


def pending(hub: httpx2.Client, chunk_id: str) -> Callable[[], bool]:
    return lambda: chunk_detail(hub, chunk_id)["pending"] is not None


def _settled(detail: dict[str, Any]) -> bool:
    return detail["status"] in {"done", "needs_human", "stopped"}


def _await(predicate: Callable[[], bool], what: str, timeout: float = _DRIVER_WAIT) -> None:
    """Wait on the hub's own flow driver until ``predicate`` holds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.5)
    raise AssertionError(f"the flow driver never converged: {what}")


def await_landed(hub: httpx2.Client, *chunk_ids: str) -> None:
    """Wait until every chunk settles, and assert each landed ``done``."""
    _await(lambda: all(_settled(chunk_detail(hub, c)) for c in chunk_ids), f"{chunk_ids} never settled")
    for chunk_id in chunk_ids:
        detail = chunk_detail(hub, chunk_id)
        assert (detail["status"], detail["landed"]) == ("done", True), (chunk_id, detail["history"])


def open_hub_db(hub_dir: Path) -> sqlite3.Connection:
    return sqlite3.connect(hub_dir / "data" / "hub.db")


def _submissions(hub_dir: Path) -> list[tuple[str, int, str | None]]:
    """Every submission as ``(chunk_id, arrival, closure outcome)``, in arrival order."""
    with open_hub_db(hub_dir) as conn:
        return conn.execute(
            "SELECT s.chunk_id, s.arrival, c.outcome FROM flow_submissions s"
            " LEFT JOIN flow_submission_closures c ON c.submission_id = s.submission_id ORDER BY s.arrival"
        ).fetchall()


def _turns(hub_dir: Path, chunk_id: str) -> list[tuple[datetime, datetime | None, str | None]]:
    """The chunk's turns as ``(started_at, ended_at, outcome)``, oldest first."""
    with open_hub_db(hub_dir) as conn:
        rows = conn.execute(
            "SELECT t.started_at, t.ended_at, t.outcome FROM hub_turns t"
            " JOIN flow_submissions s ON s.submission_id = t.submission_id WHERE s.chunk_id = ? ORDER BY t.started_at",
            (chunk_id,),
        ).fetchall()
    return [(_at(start), _at(end) if end else None, outcome) for start, end, outcome in rows]


def _at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


def _landings(hub_dir: Path) -> list[tuple[str, str]]:
    """Every ``landed`` fact as ``(chunk_id, flow resource)``, in the order they were written."""
    with open_hub_db(hub_dir) as conn:
        return conn.execute(
            "SELECT f.chunk_id, 'repo:' || w.repository_id || '/' || w.branch FROM flow_facts f"
            " JOIN flows w ON w.flow_id = f.flow_id WHERE f.kind = 'landed' ORDER BY f.id"
        ).fetchall()


def _held_flows(hub_dir: Path, chunk_id: str) -> set[str]:
    """The flows the chunk's submissions hold, by flow id."""
    with open_hub_db(hub_dir) as conn:
        rows = conn.execute(
            "SELECT h.flow_id FROM flow_submission_flows h"
            " JOIN flow_submissions s ON s.submission_id = h.submission_id WHERE s.chunk_id = ?",
            (chunk_id,),
        ).fetchall()
    return {flow_id for (flow_id,) in rows}


def _assert_flow_record_holds(hub_dir: Path, *, settled: bool = True) -> None:
    """The three flow invariants hold over every turn taken so far; once ``settled``, no turn is left open."""
    engine = create_engine_from_url(f"sqlite:///{hub_dir / 'data' / 'hub.db'}")
    try:
        violations = [v for v in hub_invariants(engine).run() if v.invariant in _FLOW_INVARIANTS]
    finally:
        engine.dispose()
    assert violations == []
    if settled:
        with open_hub_db(hub_dir) as conn:
            assert conn.execute("SELECT count(*) FROM hub_turns WHERE ended_at IS NULL").fetchone() == (0,)


def test_two_repositories_land_side_by_side(tmp_path: Path) -> None:
    """Chunks on two repositories hold different flows; both keep turning while both are held, and one lands while
    the other still waits."""
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        create_repositories(hub, [fixture_repository(WEB_REPO_NAME, f"http://127.0.0.1:{forge_port}", owner=OWNER)])
        for repo in (REPO, WEB_REPO):
            set_lever(forge, repo, armed=True)
        with ticking_runner(flow_runner_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
            api = pinned_chunk(hub, forge, "API.md", (REPO_NAME,))
            tick_until(pending(hub, api))
            web = pinned_chunk(hub, forge, "WEB.md", (WEB_REPO_NAME,))
            tick_until(pending(hub, web))
        assert _held_flows(hub_dir, api) and _held_flows(hub_dir, web)
        assert _held_flows(hub_dir, api).isdisjoint(_held_flows(hub_dir, web))

        marks = {c: len(_turns(hub_dir, c)) for c in (api, web)}
        _await(lambda: all(len(_turns(hub_dir, c)) >= marks[c] + 2 for c in (api, web)), "both flows turning")
        _assert_flow_record_holds(hub_dir, settled=False)

        set_lever(forge, WEB_REPO, armed=False)
        await_landed(hub, web)
        assert chunk_detail(hub, api)["status"] == "delivering"
        set_lever(forge, REPO, armed=False)
        await_landed(hub, api)

        assert [chunk for chunk, _ in _landings(hub_dir)] == [web, api]
        _assert_flow_record_holds(hub_dir)


def test_two_chunks_on_one_branch_land_in_arrival_order(tmp_path: Path) -> None:
    """Two chunks held on one branch come due together on a hub restarted with the checks green: they land in the
    order they arrived."""
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()

    with run_forge(bin_dir, origins, forge_port) as forge:
        with run_hub(hub_dir, forge_port, hub_port) as hub:
            set_lever(forge, REPO, armed=True)
            with ticking_runner(flow_runner_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
                first = pinned_chunk(hub, forge, "FIRST.md")
                tick_until(pending(hub, first))
                second = pinned_chunk(hub, forge, "SECOND.md")
                tick_until(pending(hub, second))
            assert _held_flows(hub_dir, first) == _held_flows(hub_dir, second)
        # Down while the checks go green, so both visits are due on the first tick back.
        set_lever(forge, REPO, armed=False)
        with run_hub(hub_dir, forge_port, hub_port, rehost=True) as hub:
            await_landed(hub, first, second)

    arrivals = [chunk for chunk, _, _ in _submissions(hub_dir)]
    assert arrivals == [first, second]
    assert [chunk for chunk, _ in _landings(hub_dir)] == arrivals
    _assert_flow_record_holds(hub_dir)


def test_a_garden_delivery_lands_while_a_chunk_is_held_on_a_branch(tmp_path: Path) -> None:
    """A chunk held pending on a branch does not hold up a garden run's delivery, which holds no branch."""
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        set_lever(forge, REPO, armed=True)
        assert hub.post("/api/graphs", json={"definition_yaml": _scripted_garden_graph_yaml()}).status_code == 201
        routine = hub.post(
            "/api/routines",
            json={
                "name": "flow-garden",
                "graph_name": "garden-routine",
                "default_scope_slug": "flow-garden-clean",
                "default_model": [],
                "default_effort": None,
            },
        )
        assert routine.status_code == 201, routine.text
        with ticking_runner(flow_runner_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
            held = pinned_chunk(hub, forge, "HELD.md")
            tick_until(pending(hub, held))
            run = hub.post(
                f"/api/routines/{routine.json()['routine_id']}/run",
                json={"scope_slug": "flow-garden-clean", "mode": "full", "note": "path=clean"},
            )
            assert run.status_code == 201, run.text
            garden = run.json()["chunk_id"]
            assert hub.post(f"/api/chunks/{garden}/promote").status_code == 202
            tick_until(lambda: _settled(chunk_detail(hub, garden)))

        assert chunk_detail(hub, garden)["status"] == "done"
        assert [(h["from_node_name"], h["choice_name"]) for h in chunk_detail(hub, garden)["history"]] == [
            ("survey", "clean"),
            ("deliver", "recorded"),
        ]
        assert _held_flows(hub_dir, garden) == set()
        standing = {chunk: outcome for chunk, _, outcome in _submissions(hub_dir)}
        assert standing == {held: None, garden: "recorded"}
        assert chunk_detail(hub, held)["status"] == "delivering"
        _assert_flow_record_holds(hub_dir, settled=False)

        set_lever(forge, REPO, armed=False)
        await_landed(hub, held)
        _assert_flow_record_holds(hub_dir)


def test_two_chunks_each_landing_both_repositories_both_land(tmp_path: Path) -> None:
    """Two chunks each landing the same two repositories share both flows, and both land, in either order."""
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()
    both = (REPO_NAME, WEB_REPO_NAME)

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        create_repositories(hub, [fixture_repository(WEB_REPO_NAME, f"http://127.0.0.1:{forge_port}", owner=OWNER)])
        for repo in (REPO, WEB_REPO):
            set_lever(forge, repo, armed=True)
        with ticking_runner(flow_runner_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
            one = pinned_chunk(hub, forge, "ONE.md", both)
            tick_until(pending(hub, one))
            two = pinned_chunk(hub, forge, "TWO.md", both)
            tick_until(pending(hub, two))
        assert len(_held_flows(hub_dir, one)) == 2
        assert _held_flows(hub_dir, one) == _held_flows(hub_dir, two)
        _assert_flow_record_holds(hub_dir, settled=False)

        for repo in (REPO, WEB_REPO):
            set_lever(forge, repo, armed=False)
        await_landed(hub, one, two)

        landings = _landings(hub_dir)
        assert sorted(landings) == sorted({(c, r) for c in (one, two) for r in {r for _, r in landings}})
        assert len(landings) == 4
        _assert_flow_record_holds(hub_dir)


def test_a_pending_chunk_yields_its_branch_until_its_next_poll(tmp_path: Path) -> None:
    """A chunk polling every few seconds is not next again before its next poll: another chunk on the branch takes
    a turn in every gap between its turns."""
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()
    slow_interval = 6

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        set_lever(forge, REPO, armed=True)
        with ticking_runner(flow_runner_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
            slow = pinned_chunk(hub, forge, "SLOW.md", poll_interval=slow_interval)
            tick_until(pending(hub, slow))
            brisk = pinned_chunk(hub, forge, "BRISK.md")
            tick_until(pending(hub, brisk))
        assert _held_flows(hub_dir, slow) == _held_flows(hub_dir, brisk)

        mark = len(_turns(hub_dir, slow))
        _await(lambda: len(_turns(hub_dir, slow)) >= mark + 3, "the slow chunk polling", timeout=4 * slow_interval + 30)
        slow_turns = _turns(hub_dir, slow)[mark - 1 :]
        brisk_starts = [start for start, _, _ in _turns(hub_dir, brisk)]
        for (_, ended, outcome), (following, _, _) in itertools.pairwise(slow_turns):
            assert outcome == "pending" and ended is not None
            assert (following - ended).total_seconds() >= slow_interval - 1, slow_turns
            assert any(ended <= b < following for b in brisk_starts), (slow_turns, brisk_starts)
        _assert_flow_record_holds(hub_dir, settled=False)

        set_lever(forge, REPO, armed=False)
        await_landed(hub, slow, brisk)
        _assert_flow_record_holds(hub_dir)


def test_a_chunk_whose_runner_is_gone_lands_and_records_its_findings(tmp_path: Path) -> None:
    """With its runner gone after build, the hub alone lands the chunk and takes it through ``record-findings``."""
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        set_lever(forge, REPO, armed=True)
        with ticking_runner(flow_runner_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
            chunk = pinned_chunk(hub, forge, "GONE.md", record_findings=True)
            tick_until(pending(hub, chunk))
        set_lever(forge, REPO, armed=False)
        await_landed(hub, chunk)

        steps = [(h["from_node_name"], h["choice_name"]) for h in chunk_detail(hub, chunk)["history"]]
        assert steps[-2:] == [("deliver", "landed"), ("record-findings", "recorded")], steps
        assert [outcome for _, _, outcome in _submissions(hub_dir)] == ["landed", "recorded"]
        assert [c for c, _ in _landings(hub_dir)] == [chunk]
        _assert_flow_record_holds(hub_dir)


def _completion_buffered(config: RunnerConfig) -> bool:
    """The runner holds a build completion it has not yet handed the hub."""
    with sqlite3.connect(Path(config.db_url.removeprefix("sqlite:///"))) as conn:
        return conn.execute(
            "SELECT count(*) FROM outbound_buffer WHERE kind = 'completion.submitted' AND acked_at IS NULL"
        ).fetchone() != (0,)


def test_a_chunk_paused_at_a_hub_node_takes_no_turn_until_resumed(tmp_path: Path) -> None:
    """A chunk paused once its build completion is buffered reaches ``deliver`` paused and takes no turn there;
    once resumed it lands."""
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        config = flow_runner_config(tmp_path, workspace, bin_dir, hub_port)
        with ticking_runner(config) as tick_until:
            chunk = pinned_chunk(hub, forge, "PAUSED.md")
            tick_until(lambda: _completion_buffered(config))
            paused = hub.post(f"/api/chunks/{chunk}/pause", json={"by": "e2e"})
            assert paused.status_code == 202, paused.text
            tick_until(lambda: any(h.get("to_node_name") == "deliver" for h in chunk_detail(hub, chunk)["history"]))
        assert chunk_detail(hub, chunk)["status"] == "paused"

        # Several driver ticks pass with the chunk at the hub node: it takes no turn.
        time.sleep(8.0)
        assert _turns(hub_dir, chunk) == []
        assert chunk_detail(hub, chunk)["status"] == "paused"
        _assert_flow_record_holds(hub_dir)

        resumed = hub.post(f"/api/chunks/{chunk}/resume", json={"by": "e2e"})
        assert resumed.status_code == 202, resumed.text
        await_landed(hub, chunk)
        assert _turns(hub_dir, chunk)
        _assert_flow_record_holds(hub_dir)
