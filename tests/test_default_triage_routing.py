"""The packaged triage router drives real migrations (component tier, issue #229).

``test_migration_apply.py`` proves the migration machinery over fixture graphs; this
proves the **packaged wiring**: reconciling the shipped set mints every graph the
default graph's choices name, a default-pinned chunk claims at ``triage``, and each
authored choice does what the front door promises — ``basic`` lands the chunk at
``bas-dwf``'s ``build``, ``advanced`` at ``adv-dwf``'s ``plan`` (entry landings: neither
lane declares a ``triage`` node to name-match), and ``already-done`` closes the chunk at
the terminal with the ``triage-findings`` asset on record and nothing delivered.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from blizzard.hub.graph_sync import GraphSyncStatus, reconcile_packaged_graphs
from tests.support import build_hub, pointer_token, report_lease

pytestmark = pytest.mark.component


def _reconciled_hub(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A hub with the whole packaged set minted, the way a deploy's reconcile does."""
    hub = build_hub(tmp_path)
    outcomes = reconcile_packaged_graphs(hub.services.graph_mint, hub.services.graphs)
    assert {o.name for o in outcomes} >= {"default-delivery", "bas-dwf", "adv-dwf"}
    assert all(o.status is GraphSyncStatus.MINTED for o in outcomes), outcomes
    return hub


def _claimed_triage_chunk(hub, *, ref: str) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    """Ingest + promote + claim one default-pinned chunk; returns (chunk_id, node_id)."""
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": ref})]}
    ).json()["chunk_id"]
    hub.client.post(f"/api/chunks/{chunk_id}/promote")
    envelope = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]
    assert envelope["node"]["node_name"] == "triage"  # the front door is the entry node
    report_lease(hub, chunk_id, epoch=1, seq=1)
    return chunk_id, envelope["node"]["node_id"]


def _route(hub, chunk_id: str, node_id: str, choice: str) -> httpx.Response:  # type: ignore[no-untyped-def]
    return hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": choice,
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": node_id,
            "artifacts": [{"name": "triage-findings", "kind": "asset", "content": "routing rationale"}],
        },
    )


def test_reconciling_the_packaged_set_twice_is_idempotent(tmp_path: Path) -> None:
    hub = _reconciled_hub(tmp_path)
    again = reconcile_packaged_graphs(hub.services.graph_mint, hub.services.graphs)
    assert all(o.status is GraphSyncStatus.UP_TO_DATE for o in again), again


def test_the_basic_choice_lands_the_chunk_at_bas_dwf_build(tmp_path: Path) -> None:
    hub = _reconciled_hub(tmp_path)
    chunk_id, node_id = _claimed_triage_chunk(hub, ref="1")

    resp = _route(hub, chunk_id, node_id, "basic")

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "migrated"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    bas_dwf_id = next(g["graph_id"] for g in hub.client.get("/api/graphs").json() if g["name"] == "bas-dwf")
    assert detail["graph_id"] == bas_dwf_id
    assert detail["current_node_name"] == "build"  # entry landing — bas-dwf has no triage node
    assert detail["status"] == "ready"  # re-queued under the lane, claimable


def test_the_advanced_choice_lands_the_chunk_at_adv_dwf_plan(tmp_path: Path) -> None:
    hub = _reconciled_hub(tmp_path)
    chunk_id, node_id = _claimed_triage_chunk(hub, ref="2")

    resp = _route(hub, chunk_id, node_id, "advanced")

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "migrated"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    adv_dwf_id = next(g["graph_id"] for g in hub.client.get("/api/graphs").json() if g["name"] == "adv-dwf")
    assert detail["graph_id"] == adv_dwf_id
    assert detail["current_node_name"] == "plan"  # entry landing — adv-dwf has no triage node
    assert detail["status"] == "ready"


def test_the_already_done_choice_closes_the_chunk_without_entering_a_lane(tmp_path: Path) -> None:
    hub = _reconciled_hub(tmp_path)
    chunk_id, node_id = _claimed_triage_chunk(hub, ref="3")

    resp = _route(hub, chunk_id, node_id, "already-done")

    assert resp.status_code == 200, resp.text
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "done"
    # The evidence outlives the chunk: the rationale asset is on the record, and no
    # commit artifact exists — nothing was built or delivered on the way out.
    kinds = {a["name"]: a["kind"] for a in detail["artifacts"]}
    assert kinds.get("triage-findings") == "asset"
    assert "git_commit" not in kinds.values()
