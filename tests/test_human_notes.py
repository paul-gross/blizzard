"""A person's note on a gate resolution, a hub requeue and a restart — component tier.

Each verb stores the note it is given and the fleet's ``ChunkDetail`` carries it back; a blank or
omitted note is no note, and leaves the verb's outcome unchanged.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.hub.store import schema as s
from tests.support import build_hub, hub_scoped, pointer_token, report_escalation, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "9"}

_GATE_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build it.
    judgement:
      prompt: Assess the build.
      choices:
        pass:
          description: Built.
          to: signoff
  signoff:
    executor: runner
    judgement:
      by: human
      choices:
        approve:
          description: Approved.
          to: done
        reject:
          description: Rejected.
          to: build
"""

_ARTIFACT = {
    "name": "acme/widget",
    "kind": "git_commit",
    "repo": "acme/widget",
    "branch_name": "b",
    "commit_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
}

_NOTE = "fix the flaky test only"


def _running(hub) -> tuple[str, dict[str, str]]:  # type: ignore[no-untyped-def]
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _GATE_YAML})
    assert graph.status_code == 201, graph.text
    nodes = {n["name"]: n["node_id"] for n in graph.json()["nodes"]}
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    report_lease(hub, chunk_id, epoch=1, seq=1)
    return chunk_id, nodes


def _gated(hub) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    chunk_id, nodes = _running(hub)
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": nodes["build"],
            "artifacts": [_ARTIFACT],
        },
    )
    assert resp.json()["outcome"] == "parked_at_gate", resp.text
    return chunk_id, hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"]["decision_id"]


def _escalated(hub) -> str:  # type: ignore[no-untyped-def]
    chunk_id, _ = _running(hub)
    assert report_escalation(hub, chunk_id, epoch=1, seq=2)["applied"] == [2]
    hub.clock.advance(timedelta(seconds=1))
    return chunk_id


def _fleet_detail(hub, chunk_id: str) -> dict:  # type: ignore[no-untyped-def]
    resp = hub.client.get(f"/api/fleet/chunks/{chunk_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _stored_notes(hub, table: sa.Table) -> list[str | None]:  # type: ignore[no-untyped-def]
    with hub.engine.connect() as conn:
        return list(conn.execute(hub_scoped(sa.select(table.c.note))).scalars().all())


# --------------------------------------------------------------------------- #
# Resolution


def test_a_resolution_note_is_stored_and_read_back_on_the_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, decision_id = _gated(hub)

    resp = hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "approve", "note": _NOTE})

    assert resp.status_code == 200, resp.text
    assert resp.json()["note"] == _NOTE
    detail = _fleet_detail(hub, chunk_id)
    assert detail["decision"]["note"] == _NOTE
    [resolution] = detail["resolutions"]
    assert resolution["decision_id"] == decision_id
    assert (resolution["node_name"], resolution["epoch"], resolution["choice"]) == ("signoff", 1, "approve")
    assert (resolution["resolved_by"], resolution["note"]) == ("operator", _NOTE)
    assert resolution["resolved_at"] and resolution["overrode_red_checks"] is False


@pytest.mark.parametrize("body", [{"choice": "approve"}, {"choice": "approve", "note": "  \n "}])
def test_an_omitted_or_blank_resolution_note_stores_none_and_resolves_alike(tmp_path: Path, body: dict) -> None:
    hub = build_hub(tmp_path)
    chunk_id, decision_id = _gated(hub)

    resp = hub.client.post(f"/api/decisions/{decision_id}/resolutions", json=body)

    assert resp.status_code == 200, resp.text
    assert resp.json()["note"] is None
    assert _stored_notes(hub, s.decision_resolutions) == [None]
    detail = _fleet_detail(hub, chunk_id)
    assert detail["status"] == "running"
    assert detail["decision"]["resolved_choice"] == "approve"
    assert [r["note"] for r in detail["resolutions"]] == [None]


def test_a_losing_resolve_keeps_the_winners_note(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, decision_id = _gated(hub)
    hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "approve", "note": _NOTE})

    loser = hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "reject", "note": "no"})

    assert loser.status_code == 409
    assert _stored_notes(hub, s.decision_resolutions) == [_NOTE]
    assert [(r["choice"], r["note"]) for r in _fleet_detail(hub, chunk_id)["resolutions"]] == [("approve", _NOTE)]


def test_a_chunk_never_resolved_or_requeued_carries_empty_lists(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _ = _gated(hub)

    detail = _fleet_detail(hub, chunk_id)

    assert (detail["resolutions"], detail["requeues"]) == ([], [])


# --------------------------------------------------------------------------- #
# Requeue


def test_a_requeue_note_and_requeuer_are_stored_and_read_back(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _escalated(hub)

    resp = hub.client.post(f"/api/chunks/{chunk_id}/requeues", json={"note": _NOTE})

    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "ready"
    [requeue] = _fleet_detail(hub, chunk_id)["requeues"]
    assert (requeue["requeued_by"], requeue["note"]) == ("operator", _NOTE)
    assert requeue["requeued_at"]


@pytest.mark.parametrize("body", [None, {}, {"note": ""}, {"note": "   "}])
def test_a_requeue_without_a_note_records_the_requeuer_and_no_note(tmp_path: Path, body: dict | None) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _escalated(hub)

    resp = (
        hub.client.post(f"/api/chunks/{chunk_id}/requeues")
        if body is None
        else hub.client.post(f"/api/chunks/{chunk_id}/requeues", json=body)
    )

    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "ready"
    assert _stored_notes(hub, s.requeues) == [None]
    assert [(r["requeued_by"], r["note"]) for r in _fleet_detail(hub, chunk_id)["requeues"]] == [("operator", None)]


# --------------------------------------------------------------------------- #
# Restart


def test_a_restart_note_is_stored_and_read_back(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _ = _running(hub)

    resp = hub.client.post(f"/api/chunks/{chunk_id}/restart", json={"by": "ada", "note": _NOTE})

    assert resp.status_code == 202, resp.text
    [restart] = _fleet_detail(hub, chunk_id)["restarts"]
    assert (restart["restarted_by"], restart["note"]) == ("ada", _NOTE)


@pytest.mark.parametrize("body", [{}, {"note": " "}])
def test_a_restart_without_a_note_stores_none_and_moves_alike(tmp_path: Path, body: dict) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _ = _running(hub)

    resp = hub.client.post(f"/api/chunks/{chunk_id}/restart", json=body)

    assert resp.status_code == 202, resp.text
    assert _stored_notes(hub, s.chunk_restarts) == [None]
    detail = _fleet_detail(hub, chunk_id)
    assert detail["latest_epoch"] == 2
    assert [r["note"] for r in detail["restarts"]] == [None]


def test_notes_on_one_chunk_read_back_oldest_first(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, decision_id = _gated(hub)
    hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "approve", "note": "first"})
    for note in ("second", "third"):
        hub.clock.advance(timedelta(seconds=1))
        assert hub.client.post(f"/api/chunks/{chunk_id}/restart", json={"note": note}).status_code == 202

    detail = _fleet_detail(hub, chunk_id)

    assert [r["note"] for r in detail["resolutions"]] == ["first"]
    assert [r["note"] for r in detail["restarts"]] == ["second", "third"]
