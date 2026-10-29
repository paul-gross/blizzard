"""The packaged garden-routine graph end to end — the `test_garden_routine_e2e` scenario
of the standing e2e smoke.

The real packaged YAML is minted with only its prompts swapped for scripts the mock can
execute — nodes, edges, session pools, and the ``garden-deliver`` action all travel to
the mint verbatim — then one routine runs it through every authored path."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.store.utc import iso_utc
from tests.e2e.support import _scripted_garden_graph_yaml, _sessions_by_node
from tests.fleet_launchers import drive_until_done, mock_bin_dir, run_forge, run_hub, runner_config
from tests.fleet_world import FIXTURE_ENV, mint_world
from tests.support import free_port

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
# A scope this routine has never touched, swept by a run whose own additions are what its
# docket cites — the proof that a delivery can answer the findings it just opened.
_SCOPE_VIRGIN = "garden-virgin"
# A scope swept under an axis the target's gardening-axes registry does not declare.
_SCOPE_UNDECLARED = "garden-undeclared-axis"


def _edges(hub, chunk_id: str) -> list[tuple[str | None, str | None]]:
    """The chunk's transition history as ``(from_node_name, choice_name)`` pairs."""
    detail = hub.get(f"/api/chunks/{chunk_id}")
    assert detail.status_code == 200, detail.text
    return [(t["from_node_name"], t["choice_name"]) for t in detail.json()["history"]]


def test_garden_routine_runs_end_to_end_on_all_six_paths(tmp_path: Path) -> None:
    """Seven runs of one routine over the packaged graph: found, clean, excessive twice
    (the convergence), invalid (the bounce), virgin (a first run citing its own delta's
    refs), and no-strategy (an undeclared axis delivered as a finding)."""
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")

    scratch = tmp_path / "scratch"
    mint_world(bin_dir, scratch)
    workspace = scratch / FIXTURE_ENV / "workspace"

    forge_port, hub_port = free_port(), free_port()
    runner_dir = tmp_path / "runner"
    with (
        run_forge(bin_dir, scratch / FIXTURE_ENV / "origins", forge_port),
        run_hub(tmp_path / "hub", forge_port, hub_port) as hub,
    ):
        minted = hub.post("/api/graphs", json={"definition_yaml": _scripted_garden_graph_yaml()})
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

        # Every non-default scope this routine runs against below must already be
        # related — the run route no longer mints one of its own.
        for slug in (_SCOPE_CLEAN, _SCOPE_THICKET, _SCOPE_VIRGIN, _SCOPE_UNDECLARED):
            scope_created = hub.post("/api/scopes", json={"slug": slug, "description": ""})
            assert scope_created.status_code == 201, scope_created.text
            scope_linked = hub.put(f"/api/routines/{routine_id}/scopes/{slug}")
            assert scope_linked.status_code == 204, scope_linked.text

        config = runner_config(runner_dir, workspace, bin_dir, hub_port)
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"

        def run(path: str, scope: str) -> str:
            minted_run = hub.post(
                f"/api/routines/{routine_id}/run",
                json={"scope_slug": scope, "mode": "full", "note": f"path={path}"},
            )
            assert minted_run.status_code == 201, minted_run.text
            chunk_id = minted_run.json()["chunk_id"]
            promoted = hub.post(f"/api/chunks/{chunk_id}/promote")
            assert promoted.status_code == 202, promoted.text
            status = drive_until_done(config, hub, chunk_id, fenced, timeout=180.0)
            assert status == "done", f"{path} run did not reach done (last status {status!r}): {_edges(hub, chunk_id)}"
            return chunk_id

        def findings(scope: str) -> list[dict]:
            resp = hub.get("/api/findings", params={"routine": _ROUTINE, "scope": scope})
            assert resp.status_code == 200, resp.text
            return resp.json()["findings"]

        # -- found: survey → reconcile → propose → deliver, two findings minted -------
        runs_since = iso_utc(datetime.now(UTC) - timedelta(minutes=5))
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
        assert hub.get("/api/garden-proposals").json()["proposals"] == []

        # -- the run list and one run's own delta read the delivered `found` run back --
        runs_until = iso_utc(datetime.now(UTC) + timedelta(minutes=5))
        runs_resp = hub.get("/api/runs", params={"since": runs_since, "until": runs_until})
        assert runs_resp.status_code == 200, runs_resp.text
        (found_row,) = [r for r in runs_resp.json() if r["chunk_id"] == found_chunk]
        assert found_row["routine_name"] == _ROUTINE
        assert found_row["scope_slug"] == _SCOPE_FOUND
        assert found_row["mode"] == "full"
        assert found_row["outcome"] == ChunkStatus.DONE
        assert [d["finding_set_id"] for d in found_row["delivered"]], (
            f"found run's row did not carry the finding-set(s) it published: {found_row}"
        )

        delta_resp = hub.get(f"/api/runs/{found_chunk}")
        assert delta_resp.status_code == 200, delta_resp.text
        delta = delta_resp.json()
        assert delta["chunk_id"] == found_chunk
        assert delta["outcome"] == ChunkStatus.DONE
        (set_delta,) = delta["sets"]
        assert {(a["class"], a["locus"], a["summary"]) for a in set_delta["added"]} == {
            ("stale-docstring", "src/app.py:1", "first weed"),
            ("stale-docstring", "src/app.py:2", "second weed"),
        }
        assert set_delta["observed"] == []
        assert set_delta["gone"] == []

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
        proposals = hub.get("/api/garden-proposals").json()["proposals"]
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
        assert len(hub.get("/api/garden-proposals").json()["proposals"]) == 1  # still only the bail-out's

        # -- virgin: no live findings at all; the docket cites reconcile's own refs and
        #    materializes onto the ids this delivery minted for them -------------------
        virgin_chunk = run("virgin", _SCOPE_VIRGIN)
        assert _edges(hub, virgin_chunk) == [
            ("survey", "found"),
            ("reconcile", "converged"),
            ("propose", "proposed"),  # this run's own additions, cited by their submission-local refs
            ("deliver", "recorded"),
        ]
        virgin_rows = findings(_SCOPE_VIRGIN)
        assert {(r["class"], r["locus"]) for r in virgin_rows} == {
            ("stale-docstring", "src/app.py:1"),
            ("stale-docstring", "src/app.py:2"),
        }
        virgin_ids = {r["finding_id"] for r in virgin_rows}
        virgin_proposal = next(
            p for p in hub.get("/api/garden-proposals").json()["proposals"] if set(p["findings"]) == virgin_ids
        )
        assert virgin_proposal["class"] == "handoff"
        assert virgin_proposal["routine_name"] == _ROUTINE
        assert set(virgin_proposal["findings"]) == virgin_ids, (
            "the docket's link rows did not point at the ids this run minted"
        )

        # -- no-strategy: the target's registry declares no entry for this routine's
        #    axis; the run still delivers, the gap itself landing as the finding --------
        undeclared_chunk = run("no-strategy", _SCOPE_UNDECLARED)
        assert _edges(hub, undeclared_chunk) == [
            ("survey", "no-strategy"),
            ("reconcile", "converged"),
            ("propose", "none"),
            ("deliver", "recorded"),
        ]
        undeclared_rows = findings(_SCOPE_UNDECLARED)
        assert len(undeclared_rows) == 1, undeclared_rows
        assert undeclared_rows[0]["class"] == "undeclared-axis"
        assert undeclared_rows[0]["live"]

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
