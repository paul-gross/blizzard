"""The hub's flow driver takes every hub step as a turn on the flows of the branches it lands on, against the mock forge.

Flows on different repositories run side by side; one branch's chunks land in arrival order; a pending turn yields its
branch until its next poll; a visit holding no branch (a garden delivery, ``record-findings``) never waits on one; a
paused chunk takes no turn. Each scenario ends with the flow invariants holding and no turn open.
Needs ``BLIZZARD_E2E=1`` and ``blizzard-mock``."""

from __future__ import annotations

import dataclasses
import itertools
import os
import sqlite3
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest
import yaml

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop_wiring import LoopWiring
from blizzard.tools.invariants import hub_invariants
from tests.e2e.support import _scripted_garden_graph_yaml
from tests.fleet_launchers import OWNER, REPO, mock_bin_dir, run_forge, run_hub, runner_api, runner_config
from tests.fleet_world import FIXTURE_ENV, REPO_NAME, basic_workspace, mint_world
from tests.support import create_repositories, fixture_repository, free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e flow turns need the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

_WEB_REPO_NAME = "toy-web"
_WEB_REPO = f"{OWNER}/{_WEB_REPO_NAME}"
_FLOW_INVARIANTS = frozenset({"hub:one-turn-per-flow", "hub:one-turn-per-visit", "hub:flow-line-standing"})
#: Comfortably past the driver's two-second tick, so a converged line is never a race.
_DRIVER_WAIT = 60.0
_LAND_CHOICES = {
    "landed": {"description": "Every repo's PR merged cleanly.", "to": "done"},
    "conflict": {"description": "A repo's PR is dirty; back to build.", "to": "build"},
    "failure": {"description": "A repo could not land; back to build.", "to": "build"},
    "inherited-failure": {"description": "The base check stays red; back to build.", "to": "build"},
}
_RECORD_FINDINGS = {
    "executor": "hub",
    "action": "record-findings",
    "judgement": {
        "choices": {
            "recorded": {"description": "The deferred findings are durable.", "to": "done"},
            "invalid": {"description": "The delta failed validation.", "to": "done"},
            "failure": {"description": "The record was refused.", "to": "done"},
        }
    },
}


_PUBLISH_DELTA = (
    "subprocess.run(['blizzard', 'runner', 'artifact', 'create', '--name', 'review-finding-delta'],\n"
    '               input=\'{"entries": [{"ref": "F1", "disposition": "fixed"}]}\', text=True, check=True)\n'
)


def _build_script(landed_file: str, repos: Sequence[str], *, delta: bool) -> str:
    """A build that commits ``landed_file`` in each of ``repos`` on a branch named for it, pushes, and declares it —
    then, with ``delta``, publishes the review-finding delta ``record-findings`` reads."""
    branch = f"feature/{Path(landed_file).stem.lower()}"
    return (
        "import os, pathlib, subprocess\n"
        "root = pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0])\n"
        f"for name in {list(repos)!r}:\n"
        "    path = str(root / name)\n"
        f"    subprocess.run(['git', '-C', path, 'switch', '-C', {branch!r}], check=True)\n"
        f"    (root / name / {landed_file!r}).write_text('held on a flow\\n')\n"
        "    subprocess.run(['git', '-C', path, 'add', '-A'], check=True)\n"
        "    subprocess.run(['git', '-C', path, '-c', 'user.email=mock@blizzard.local',\n"
        f"                    '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: {landed_file}'], check=True)\n"
        "    commit = subprocess.run(['git', '-C', path, 'rev-parse', 'HEAD'],\n"
        "                            check=True, capture_output=True, text=True).stdout.strip()\n"
        f"    subprocess.run(['git', '-C', path, 'push', '--force', 'origin', {branch!r}], check=True)\n"
        "    subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', name,\n"
        f"                    '--branch', {branch!r}, '--commit', commit], check=True)\n"
    ) + (_PUBLISH_DELTA if delta else "")


def _graph(landed_file: str, repos: Sequence[str], *, poll_interval: int, record_findings: bool) -> str:
    """A graph whose build commits ``landed_file`` in ``repos`` and whose ``deliver`` lands it, re-polling every
    ``poll_interval`` seconds — then ``record-findings`` when asked."""
    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _build_script(landed_file, repos, delta=record_findings),
                "judgement": {
                    "prompt": "verdict('pass', 'the mock harness did its turn')\n",
                    "choices": {"pass": {"description": "Built.", "to": "deliver"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "action": "land",
                "poll_interval": poll_interval,
                "poll_timeout": 600,
                "judgement": {"choices": _LAND_CHOICES},
            },
        },
    }
    if record_findings:
        graph["nodes"]["deliver"]["judgement"] = {
            "choices": {**_LAND_CHOICES, "landed": {**_LAND_CHOICES["landed"], "to": "record-findings"}}
        }
        graph["nodes"]["build"]["produces"] = ["review-finding-delta"]
        graph["nodes"]["record-findings"] = _RECORD_FINDINGS
    return yaml.safe_dump(graph, sort_keys=False)


def _pinned_chunk(
    hub: httpx2.Client,
    forge: httpx2.Client,
    landed_file: str,
    repos: Sequence[str] = (REPO_NAME,),
    *,
    poll_interval: int = 1,
    record_findings: bool = False,
) -> str:
    """Mint the graph landing ``landed_file`` in ``repos``, ingest one chunk pinned to it, and promote it."""
    graph = _graph(landed_file, repos, poll_interval=poll_interval, record_findings=record_findings)
    assert hub.post("/api/graphs", json={"definition_yaml": graph}).status_code == 201
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": f"lands {landed_file}"})
    assert issue.status_code == 201, issue.text
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    return chunk_id


@contextmanager
def _runner(config: RunnerConfig) -> Iterator[Callable[[Callable[[], bool]], None]]:
    """A live runner; yields ``tick_until(predicate)``, which ticks it until ``predicate`` holds."""
    prior = dict(os.environ)
    os.environ["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"

    def tick_until(predicate: Callable[[], bool]) -> None:
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            LoopWiring.of(config).tick_once()
            if predicate():
                return
            time.sleep(0.4)
        raise AssertionError("the runner never brought the chunks to the awaited state")

    try:
        with runner_api(config):
            yield tick_until
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _detail(hub: httpx2.Client, chunk_id: str) -> dict[str, Any]:
    return hub.get(f"/api/chunks/{chunk_id}").json()


def _pending(hub: httpx2.Client, chunk_id: str) -> Callable[[], bool]:
    return lambda: _detail(hub, chunk_id)["pending"] is not None


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


def _await_landed(hub: httpx2.Client, *chunk_ids: str) -> None:
    """Wait until every chunk settles, and assert each landed ``done``."""
    _await(lambda: all(_settled(_detail(hub, c)) for c in chunk_ids), f"{chunk_ids} never settled")
    for chunk_id in chunk_ids:
        detail = _detail(hub, chunk_id)
        assert (detail["status"], detail["landed"]) == ("done", True), (chunk_id, detail["history"])


def _set_lever(forge: httpx2.Client, repo: str, *, armed: bool) -> None:
    if armed:
        assert forge.post("/_levers/checks_pending", json={"repo": repo}).status_code == 200
    else:
        assert forge.delete("/_levers/checks_pending", params={"repo": repo}).status_code == 200


def _store(hub_dir: Path) -> sqlite3.Connection:
    return sqlite3.connect(hub_dir / "data" / "hub.db")


def _submissions(hub_dir: Path) -> list[tuple[str, int, str | None]]:
    """Every submission as ``(chunk_id, arrival, closure outcome)``, in arrival order."""
    with _store(hub_dir) as conn:
        return conn.execute(
            "SELECT s.chunk_id, s.arrival, c.outcome FROM flow_submissions s"
            " LEFT JOIN flow_submission_closures c ON c.submission_id = s.submission_id ORDER BY s.arrival"
        ).fetchall()


def _turns(hub_dir: Path, chunk_id: str) -> list[tuple[datetime, datetime | None, str | None]]:
    """The chunk's turns as ``(started_at, ended_at, outcome)``, oldest first."""
    with _store(hub_dir) as conn:
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
    with _store(hub_dir) as conn:
        return conn.execute(
            "SELECT f.chunk_id, 'repo:' || w.repository_id || '/' || w.branch FROM flow_facts f"
            " JOIN flows w ON w.flow_id = f.flow_id WHERE f.kind = 'landed' ORDER BY f.id"
        ).fetchall()


def _held_flows(hub_dir: Path, chunk_id: str) -> set[str]:
    """The flows the chunk's submissions hold, by flow id."""
    with _store(hub_dir) as conn:
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
        with _store(hub_dir) as conn:
            assert conn.execute("SELECT count(*) FROM hub_turns WHERE ended_at IS NULL").fetchone() == (0,)


def _world(tmp_path: Path) -> tuple[Path, Path, Path]:
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    mint_world(bin_dir, tmp_path / "scratch")
    fixture_root = tmp_path / "scratch" / FIXTURE_ENV
    return bin_dir, fixture_root / "workspace", fixture_root / "origins"


def _config(tmp_path: Path, workspace: Path, bin_dir: Path, hub_port: int) -> RunnerConfig:
    """A runner with two environments, so a chunk held at ``deliver`` leaves the next a place to build."""
    return dataclasses.replace(
        runner_config(tmp_path / "runner", workspace, bin_dir, hub_port),
        workspaces=(dataclasses.replace(basic_workspace(workspace), max_environments=2),),
    )


def test_two_repositories_land_side_by_side(tmp_path: Path) -> None:
    """Chunks on two repositories hold different flows; both keep turning while both are held, and one lands while
    the other still waits."""
    bin_dir, workspace, origins = _world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        create_repositories(hub, [fixture_repository(_WEB_REPO_NAME, f"http://127.0.0.1:{forge_port}", owner=OWNER)])
        for repo in (REPO, _WEB_REPO):
            _set_lever(forge, repo, armed=True)
        with _runner(_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
            api = _pinned_chunk(hub, forge, "API.md", (REPO_NAME,))
            tick_until(_pending(hub, api))
            web = _pinned_chunk(hub, forge, "WEB.md", (_WEB_REPO_NAME,))
            tick_until(_pending(hub, web))
        assert _held_flows(hub_dir, api) and _held_flows(hub_dir, web)
        assert _held_flows(hub_dir, api).isdisjoint(_held_flows(hub_dir, web))

        marks = {c: len(_turns(hub_dir, c)) for c in (api, web)}
        _await(lambda: all(len(_turns(hub_dir, c)) >= marks[c] + 2 for c in (api, web)), "both flows turning")
        _assert_flow_record_holds(hub_dir, settled=False)

        _set_lever(forge, _WEB_REPO, armed=False)
        _await_landed(hub, web)
        assert _detail(hub, api)["status"] == "delivering"
        _set_lever(forge, REPO, armed=False)
        _await_landed(hub, api)

        assert [chunk for chunk, _ in _landings(hub_dir)] == [web, api]
        _assert_flow_record_holds(hub_dir)


def test_two_chunks_on_one_branch_land_in_arrival_order(tmp_path: Path) -> None:
    """Two chunks held on one branch come due together on a hub restarted with the checks green: they land in the
    order they arrived."""
    bin_dir, workspace, origins = _world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()

    with run_forge(bin_dir, origins, forge_port) as forge:
        with run_hub(hub_dir, forge_port, hub_port) as hub:
            _set_lever(forge, REPO, armed=True)
            with _runner(_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
                first = _pinned_chunk(hub, forge, "FIRST.md")
                tick_until(_pending(hub, first))
                second = _pinned_chunk(hub, forge, "SECOND.md")
                tick_until(_pending(hub, second))
            assert _held_flows(hub_dir, first) == _held_flows(hub_dir, second)
        # Down while the checks go green, so both visits are due on the first tick back.
        _set_lever(forge, REPO, armed=False)
        with run_hub(hub_dir, forge_port, hub_port, rehost=True) as hub:
            _await_landed(hub, first, second)

    arrivals = [chunk for chunk, _, _ in _submissions(hub_dir)]
    assert arrivals == [first, second]
    assert [chunk for chunk, _ in _landings(hub_dir)] == arrivals
    _assert_flow_record_holds(hub_dir)


def test_a_garden_delivery_lands_while_a_chunk_is_held_on_a_branch(tmp_path: Path) -> None:
    """A chunk held pending on a branch does not hold up a garden run's delivery, which holds no branch."""
    bin_dir, workspace, origins = _world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        _set_lever(forge, REPO, armed=True)
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
        with _runner(_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
            held = _pinned_chunk(hub, forge, "HELD.md")
            tick_until(_pending(hub, held))
            run = hub.post(
                f"/api/routines/{routine.json()['routine_id']}/run",
                json={"scope_slug": "flow-garden-clean", "mode": "full", "note": "path=clean"},
            )
            assert run.status_code == 201, run.text
            garden = run.json()["chunk_id"]
            assert hub.post(f"/api/chunks/{garden}/promote").status_code == 202
            tick_until(lambda: _settled(_detail(hub, garden)))

        assert _detail(hub, garden)["status"] == "done"
        assert [(h["from_node_name"], h["choice_name"]) for h in _detail(hub, garden)["history"]] == [
            ("survey", "clean"),
            ("deliver", "recorded"),
        ]
        assert _held_flows(hub_dir, garden) == set()
        standing = {chunk: outcome for chunk, _, outcome in _submissions(hub_dir)}
        assert standing == {held: None, garden: "recorded"}
        assert _detail(hub, held)["status"] == "delivering"
        _assert_flow_record_holds(hub_dir, settled=False)

        _set_lever(forge, REPO, armed=False)
        _await_landed(hub, held)
        _assert_flow_record_holds(hub_dir)


def test_two_chunks_each_landing_both_repositories_both_land(tmp_path: Path) -> None:
    """Two chunks each landing the same two repositories share both flows, and both land, in either order."""
    bin_dir, workspace, origins = _world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()
    both = (REPO_NAME, _WEB_REPO_NAME)

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        create_repositories(hub, [fixture_repository(_WEB_REPO_NAME, f"http://127.0.0.1:{forge_port}", owner=OWNER)])
        for repo in (REPO, _WEB_REPO):
            _set_lever(forge, repo, armed=True)
        with _runner(_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
            one = _pinned_chunk(hub, forge, "ONE.md", both)
            tick_until(_pending(hub, one))
            two = _pinned_chunk(hub, forge, "TWO.md", both)
            tick_until(_pending(hub, two))
        assert len(_held_flows(hub_dir, one)) == 2
        assert _held_flows(hub_dir, one) == _held_flows(hub_dir, two)
        _assert_flow_record_holds(hub_dir, settled=False)

        for repo in (REPO, _WEB_REPO):
            _set_lever(forge, repo, armed=False)
        _await_landed(hub, one, two)

        landings = _landings(hub_dir)
        assert sorted(landings) == sorted({(c, r) for c in (one, two) for r in {r for _, r in landings}})
        assert len(landings) == 4
        _assert_flow_record_holds(hub_dir)


def test_a_pending_chunk_yields_its_branch_until_its_next_poll(tmp_path: Path) -> None:
    """A chunk polling every few seconds is not next again before its next poll: another chunk on the branch takes
    a turn in every gap between its turns."""
    bin_dir, workspace, origins = _world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()
    slow_interval = 6

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        _set_lever(forge, REPO, armed=True)
        with _runner(_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
            slow = _pinned_chunk(hub, forge, "SLOW.md", poll_interval=slow_interval)
            tick_until(_pending(hub, slow))
            brisk = _pinned_chunk(hub, forge, "BRISK.md")
            tick_until(_pending(hub, brisk))
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

        _set_lever(forge, REPO, armed=False)
        _await_landed(hub, slow, brisk)
        _assert_flow_record_holds(hub_dir)


def test_a_chunk_whose_runner_is_gone_lands_and_records_its_findings(tmp_path: Path) -> None:
    """With its runner gone after build, the hub alone lands the chunk and takes it through ``record-findings``."""
    bin_dir, workspace, origins = _world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        _set_lever(forge, REPO, armed=True)
        with _runner(_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
            chunk = _pinned_chunk(hub, forge, "GONE.md", record_findings=True)
            tick_until(_pending(hub, chunk))
        _set_lever(forge, REPO, armed=False)
        _await_landed(hub, chunk)

        steps = [(h["from_node_name"], h["choice_name"]) for h in _detail(hub, chunk)["history"]]
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
    bin_dir, workspace, origins = _world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        config = _config(tmp_path, workspace, bin_dir, hub_port)
        with _runner(config) as tick_until:
            chunk = _pinned_chunk(hub, forge, "PAUSED.md")
            tick_until(lambda: _completion_buffered(config))
            paused = hub.post(f"/api/chunks/{chunk}/pause", json={"by": "e2e"})
            assert paused.status_code == 202, paused.text
            tick_until(lambda: any(h.get("to_node_name") == "deliver" for h in _detail(hub, chunk)["history"]))
        assert _detail(hub, chunk)["status"] == "paused"

        # Several driver ticks pass with the chunk at the hub node: it takes no turn.
        time.sleep(8.0)
        assert _turns(hub_dir, chunk) == []
        assert _detail(hub, chunk)["status"] == "paused"
        _assert_flow_record_holds(hub_dir)

        resumed = hub.post(f"/api/chunks/{chunk}/resume", json={"by": "e2e"})
        assert resumed.status_code == 202, resumed.text
        _await_landed(hub, chunk)
        assert _turns(hub_dir, chunk)
        _assert_flow_record_holds(hub_dir)
