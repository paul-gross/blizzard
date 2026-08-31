"""The packaged garden-routine graph end to end — the `test_garden_routine_e2e` scenario
of the standing e2e smoke (blizzard#396).

The real packaged YAML is minted with only its prompts swapped for scripts the mock can
execute — nodes, edges, session pools, and the ``garden_deliver`` command all travel to
the mint verbatim — then one routine runs it through every authored path."""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from blizzard.hub.graphs import PACKAGED
from tests.e2e.test_acceptance_loop import (
    FIXTURE_ENV,
    _drive_until_done,
    _forge,
    _free_port,
    _hub,
    _mock_bin_dir,
    _runner_config,
    _winter_source,
)
from tests.e2e.test_session_modes_e2e import _sessions_by_node

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e garden routine needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

_ROUTINE = "garden-e2e"

# The scripted prompts branch on the run's `--note`, which lands in the work item body
# as a "This run" section — each run's path rides the machinery a real note would.
_SCOPE_FOUND = "garden-found"
_SCOPE_CLEAN = "garden-clean"
_SCOPE_THICKET = "garden-thicket"

# A well-formed fin_<ULID> that is live on no routine — delivery must reject it as
# unknown, not as malformed.
_UNKNOWN_FINDING_ID = "fin_0" + "Z" * 25


def _common(hub_port: int) -> str:
    """The shared preamble: the charge parsed off the real work item, the worker
    artifact verbs, and the bucket read the packaged reconcile prompt names."""
    return (
        "import json, os, pathlib, subprocess\n"
        "chunk_id = os.environ['BLIZZARD_CHUNK_ID']\n"
        "def sh(*args, inp=None, env_extra=None):\n"
        "    env = dict(os.environ)\n"
        "    if env_extra: env.update(env_extra)\n"
        "    return subprocess.run(list(args), input=inp, check=True, capture_output=True, text=True, env=env).stdout\n"
        "item = json.loads(sh('blizzard', 'runner', 'work-items', chunk_id))['items'][0]\n"
        "lines = item['body'].splitlines()\n"
        "path = next(l.split('=', 1)[1] for l in lines if l.startswith('path='))\n"
        "scope = next(l for l in lines if l.startswith('Scope:')).split()[1]\n"
        "def publish(name, content):\n"
        "    sh('blizzard', 'runner', 'artifact', 'create', '--name', name, inp=content)\n"
        "def bucket():\n"
        "    rows = json.loads(sh('blizzard', 'hub', 'finding', 'list', '--routine', "
        f"{_ROUTINE!r}, '--scope', scope, '--json', env_extra={{'BZ_HUB_URL': 'http://127.0.0.1:{hub_port}'}}))\n"
        "    assert all(r['scope_slug'] == scope for r in rows), f'bucket leaked another scope: {rows}'\n"
        "    return [r for r in rows if r['live']]\n"
        "addendum_marker = pathlib.Path(f'.garden-addendum-{chunk_id}')\n"
    )


# survey: read the finding format from system scope (as the packaged prompt directs),
# then publish the envelope and the run's delta skeleton.
_SURVEY = (
    "rev = sh('git', '-C', 'toy-api', 'rev-parse', 'HEAD').strip()\n"
    "fmt = sh('blizzard', 'runner', 'artifact', 'get', '--scope', 'system', 'garden/finding-format', '--content')\n"
    "assert 'FindingDelta' in fmt, 'system-scope finding format did not resolve'\n"
    "if path in ('found', 'invalid'):\n"
    "    cands = [\n"
    "        {'ref': 'F1', 'class': 'stale-docstring', 'locus': 'src/app.py:1', 'summary': 'first weed'},\n"
    "        {'ref': 'F2', 'class': 'stale-docstring', 'locus': 'src/app.py:2', 'summary': 'second weed'},\n"
    "    ]\n"
    "elif path == 'excessive':\n"
    "    cands = [{'ref': 'F1', 'class': 'excessive-scope', 'locus': scope,\n"
    "              'summary': 'roughly four hundred instances across the scope'}]\n"
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
    "choice = {'found': 'found', 'invalid': 'found', 'excessive': 'excessive', 'clean': 'clean'}[path]\n"
    "verdict(choice, 'scripted sweep')\n"
)

# reconcile: match the envelope's candidates against the live bucket. On the invalid
# path the bad delta is republished until the addendum has run, so the corrected delta
# exists only if the `invalid` edge's addendum actually threaded.
_RECONCILE = (
    "envelope = json.loads(sh('blizzard', 'runner', 'artifact', 'get', 'survey', '--content'))\n"
    "live = bucket()\n"
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
    "            ops.append({'op': 'add', 'class': cand['class'], 'locus': cand['locus'],\n"
    "                        'summary': cand['summary']})\n"
    "    delta = {'scope': scope, 'revisions': envelope['revisions'], 'measurement': envelope['measurement'],\n"
    "             'findings': ops}\n"
    "publish('delta', json.dumps(delta))\n"
)

_RECONCILE_JUDGEMENT = "verdict('nothing-to-propose' if addendum_marker.exists() else 'converged', 'scripted match')\n"

# The `invalid` edge's addendum, appended to reconcile's re-entry program: read the
# failure the rejected delivery recorded, replace the staged delta, leave the marker.
_RECONCILE_FROM_DELIVER = (
    "failure = sh('blizzard', 'runner', 'artifact', 'get', 'garden-delivery-failure', '--content')\n"
    "assert 'fin_' in failure, f'failure artifact does not name the rejected id: {failure!r}'\n"
    "good = {'scope': scope, 'revisions': envelope['revisions'], 'measurement': envelope['measurement'],\n"
    "        'findings': [{'op': 'observed', 'id': f['finding_id']} for f in live]}\n"
    "publish('delta', json.dumps(good))\n"
    "addendum_marker.write_text('threaded')\n"
)

# propose: cite every live finding in the bucket, or publish the empty docket — a run's
# own additions are not live until delivery, so only an earlier run's are citable.
_PROPOSE = (
    "fmt = sh('blizzard', 'runner', 'artifact', 'get', '--scope', 'system', 'garden/proposal-format', '--content')\n"
    "assert 'GardenProposalCandidate' in fmt, 'system-scope proposal format did not resolve'\n"
    "live = bucket()\n"
    "if path == 'invalid' or not live:\n"
    "    docket = []\n"
    "else:\n"
    "    docket = [{'ref': 'P1', 'class': 'handoff', 'title': f'hand {scope} out of the fleet',\n"
    "               'body': 'scripted proposal citing every live finding in the bucket',\n"
    "               'findings': [f['finding_id'] for f in live]}]\n"
    "publish('docket', json.dumps(docket))\n"
)

_PROPOSE_JUDGEMENT = "verdict('proposed' if (path != 'invalid' and bucket()) else 'none', 'scripted propose')\n"


def _scripted_garden_graph_yaml(hub_port: int) -> str:
    """The real packaged ``garden-routine`` body with only its prompts swapped for
    scripts — nodes, edges, session pools, and the deliver command stay verbatim."""
    body = PACKAGED.named("garden-routine").body
    common = _common(hub_port)
    nodes: Any = body["nodes"]
    nodes["survey"]["prompt"] = common + _SURVEY
    nodes["survey"]["judgement"]["prompt"] = common + _SURVEY_JUDGEMENT
    nodes["reconcile"]["prompt"] = common + _RECONCILE
    nodes["reconcile"]["judgement"]["prompt"] = common + _RECONCILE_JUDGEMENT
    nodes["propose"]["prompt"] = common + _PROPOSE
    nodes["propose"]["judgement"]["prompt"] = common + _PROPOSE_JUDGEMENT
    nodes["deliver"]["judgement"]["choices"]["invalid"]["prompt_addendum"] = _RECONCILE_FROM_DELIVER
    return yaml.safe_dump(body, sort_keys=False)


def _edges(hub, chunk_id: str) -> list[tuple[str | None, str | None]]:
    """The chunk's transition history as ``(from_node_name, choice_name)`` pairs."""
    detail = hub.get(f"/api/chunks/{chunk_id}")
    assert detail.status_code == 200, detail.text
    return [(t["from_node_name"], t["choice_name"]) for t in detail.json()["history"]]


def test_garden_routine_runs_end_to_end_on_all_four_paths(tmp_path: Path) -> None:
    """Five runs of one routine over the packaged graph: found, clean, excessive twice
    (the convergence), and invalid (the rejected delivery's bounce)."""
    bin_dir = _mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    winter_source = _winter_source()
    if winter_source is None:
        pytest.skip("no local winter source (set BLIZZARD_MOCK_WINTER_SOURCE)")

    scratch = tmp_path / "scratch"
    subprocess.run(
        [
            str(bin_dir / "blizzard-mock-fixture"),
            "reset",
            "--env",
            FIXTURE_ENV,
            "--scratch-root",
            str(scratch),
            "--winter-source",
            str(winter_source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    workspace = scratch / FIXTURE_ENV / "workspace"
    (workspace / ".blizzard-mock-harness-fence").write_text("e2e fence marker\n")

    forge_port, hub_port = _free_port(), _free_port()
    runner_dir = tmp_path / "runner"
    with (
        _forge(bin_dir, scratch / FIXTURE_ENV / "origins", forge_port),
        _hub(tmp_path / "hub", forge_port, hub_port) as hub,
    ):
        minted = hub.post("/api/graphs", json={"definition_yaml": _scripted_garden_graph_yaml(hub_port)})
        assert minted.status_code == 201, minted.text

        created = hub.post(
            "/api/routines",
            json={
                "name": _ROUTINE,
                "graph_name": "garden-routine",
                "default_scope_slug": _SCOPE_FOUND,
                "default_model": [],
                "default_effort": None,
            },
        )
        assert created.status_code == 201, created.text
        routine_id = created.json()["routine_id"]

        config = dataclasses.replace(_runner_config(runner_dir, workspace, bin_dir, hub_port), max_agents=1)
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"

        def run(path: str, scope: str) -> str:
            minted_run = hub.post(
                f"/api/routines/{routine_id}/run",
                json={"scope_slug": scope, "mode": "full", "note": f"path={path}"},
            )
            assert minted_run.status_code == 201, minted_run.text
            chunk_id = minted_run.json()["chunk_id"]
            status = _drive_until_done(config, hub, chunk_id, fenced, timeout=180.0)
            assert status == "done", f"{path} run did not reach done (last status {status!r}): {_edges(hub, chunk_id)}"
            return chunk_id

        def findings(scope: str) -> list[dict]:
            resp = hub.get("/api/findings", params={"routine": _ROUTINE, "scope": scope})
            assert resp.status_code == 200, resp.text
            return resp.json()

        # -- found: survey → reconcile → propose → deliver, two findings minted -------
        found_chunk = run("found", _SCOPE_FOUND)
        assert _edges(hub, found_chunk) == [
            ("survey", "found"),
            ("reconcile", "converged"),
            ("propose", "none"),  # a first run's additions are not live yet, so no proposal can cite them
            ("deliver", "recorded"),
        ]
        found_rows = findings(_SCOPE_FOUND)
        assert {(r["class"], r["locus"]) for r in found_rows} == {
            ("stale-docstring", "src/app.py:1"),
            ("stale-docstring", "src/app.py:2"),
        }
        assert all(r["live"] for r in found_rows)
        assert hub.get("/api/garden-proposals").json() == []

        # -- clean: survey straight to deliver; the empty delta's datapoint recorded --
        clean_chunk = run("clean", _SCOPE_CLEAN)
        assert _edges(hub, clean_chunk) == [("survey", "clean"), ("deliver", "recorded")]
        assert findings(_SCOPE_CLEAN) == []
        with sqlite3.connect(f"file:{tmp_path / 'hub' / 'data' / 'hub.db'}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "select measurement, revisions from finding_sets where scope_slug = ?", (_SCOPE_CLEAN,)
            ).fetchone()
        assert row is not None, "clean run recorded no finding set — its datapoint is its product"
        assert row[0] == f"e2e sweep of {_SCOPE_CLEAN}: 0 flagged"
        assert "toy-api" in json.loads(row[1])

        # -- excessive, twice: the bail-out converges instead of accreting -------------
        first = run("excessive", _SCOPE_THICKET)
        assert _edges(hub, first) == [
            ("survey", "excessive"),
            ("reconcile", "converged"),
            ("propose", "none"),
            ("deliver", "recorded"),
        ]
        thicket_rows = findings(_SCOPE_THICKET)
        assert len(thicket_rows) == 1 and thicket_rows[0]["class"] == "excessive-scope"
        bailout_id = thicket_rows[0]["finding_id"]
        observed_before = thicket_rows[0]["observed_count"]

        second = run("excessive", _SCOPE_THICKET)
        assert _edges(hub, second) == [
            ("survey", "excessive"),
            ("reconcile", "converged"),
            ("propose", "proposed"),  # the bail-out is live now, so the hand-out proposal can cite it
            ("deliver", "recorded"),
        ]
        thicket_rows = findings(_SCOPE_THICKET)
        assert [r["finding_id"] for r in thicket_rows] == [bailout_id], (
            f"the second bail-out minted a new finding instead of observing the live one: {thicket_rows}"
        )
        assert thicket_rows[0]["observed_count"] == observed_before + 1
        proposals = hub.get("/api/garden-proposals").json()
        assert len(proposals) == 1, proposals
        assert proposals[0]["class"] == "handoff"
        assert proposals[0]["routine_name"] == _ROUTINE
        assert proposals[0]["findings"] == [bailout_id]

        # -- invalid: delivery rejects the unknown id, bounces, and the addendum's
        #    corrected delta delivers ------------------------------------------------
        found_before = {r["finding_id"]: r["observed_count"] for r in findings(_SCOPE_FOUND)}
        invalid_chunk = run("invalid", _SCOPE_FOUND)
        assert _edges(hub, invalid_chunk) == [
            ("survey", "found"),
            ("reconcile", "converged"),
            ("propose", "none"),
            ("deliver", "invalid"),  # nothing written; bounced to reconcile with the addendum
            ("reconcile", "nothing-to-propose"),
            ("deliver", "recorded"),
        ]
        found_after = {r["finding_id"]: r["observed_count"] for r in findings(_SCOPE_FOUND)}
        assert set(found_after) == set(found_before), "the rejected delivery leaked a write"
        assert all(found_after[fid] == count + 1 for fid, count in found_before.items()), (
            f"the corrected delta's observed ops did not land: {found_before} -> {found_after}"
        )
        assert len(hub.get("/api/garden-proposals").json()) == 1  # still only the bail-out's

    # -- the load-bearing session policy, off the runner's own store -----------------
    db_url = config.db_url
    for chunk_id in (found_chunk, first, second):
        by_node = _sessions_by_node(db_url, chunk_id)
        (survey_sid,) = by_node["survey"]
        (reconcile_sid,) = by_node["reconcile"]
        (propose_sid,) = by_node["propose"]
        assert reconcile_sid != survey_sid, "reconcile did not get cold eyes — it shares survey's session"
        assert propose_sid == reconcile_sid, "propose did not resume the match session holding the delta"

    by_node = _sessions_by_node(db_url, invalid_chunk)
    assert len(by_node["reconcile"]) == 2, by_node
    assert len(set(by_node["reconcile"])) == 2, "the bounced reconcile re-entry did not mint a fresh match session"
    (propose_sid,) = by_node["propose"]
    assert propose_sid == by_node["reconcile"][0], "propose did not resume the match head its reconcile minted"
