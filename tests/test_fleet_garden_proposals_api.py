"""``GET /api/fleet/chunks/{chunk_id}/garden/proposals`` — the worker-scoped fleet read
of a routine's open garden-proposal docket (component tier). Derives the routine from the
chunk's own ``RunContext``, reuses ``garden_proposals.py``'s own ``proposal_view``, and
refuses — rather than answering an empty bucket — an unknown chunk or one with no run
context. No scope column exists here, so unlike findings there is no scope case, only
the closed-proposal exclusion."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.hub.domain.run_context import RunContext
from blizzard.hub.domain.work import WorkItemAuthor
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.garden_proposal_closure_store import GardenProposalClosureStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.hub.store.internal.run_context_store import RunContextStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import HubHarness, build_hub, hub_store_connections, seed_work_item

pytestmark = pytest.mark.component

_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
_ROUTINE = "nightly"
_SCOPE = "blizzard"


def _seed_chunk(hub: HubHarness, *, with_run_context: bool = True) -> str:
    """A work item with its own resting chunk, plus a recorded run context for it
    unless ``with_run_context`` is False — the chunk id the route resolves through."""
    store_connections = hub_store_connections(hub.engine)
    items = WorkItemStore(store_connections)
    item = seed_work_item(items, graph_id="gr_garden", author=WorkItemAuthor.user("u_1"), at=_NOW)
    if with_run_context:
        RunContextStore(store_connections).record(
            item.work_item_id, RunContext(routine_name=_ROUTINE, scope_slug=_SCOPE, mode="dry_run")
        )
    return f"ch_{item.ref}"


def _seed_finding(hub: HubHarness, finding_id: str) -> None:
    FindingStore(hub_store_connections(hub.engine)).add(
        finding_id,
        routine_name=_ROUTINE,
        scope_slug=_SCOPE,
        class_="stale-docstring",
        locus="a.py:1",
        summary="s",
        introduced=None,
        at=_NOW,
    )


def _seed_proposal(hub: HubHarness, proposal_id: str, *, routine_name: str = _ROUTINE) -> None:
    GardenProposalStore(hub_store_connections(hub.engine)).create(
        proposal_id,
        routine_name=routine_name,
        class_="fix-the-source",
        title="t",
        body="b",
        findings=["fin_1"],
        at=_NOW,
    )


def _pass_proposal(hub: HubHarness, proposal_id: str) -> None:
    GardenProposalClosureStore(hub_store_connections(hub.engine)).record_pass(
        proposal_id, reason="not worth it", closed_by="u_1", at=_NOW
    )


def test_404s_on_an_unknown_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.get("/api/fleet/chunks/ch_ghost/garden/proposals")
    assert resp.status_code == 404, resp.text
    assert "unknown chunk" in resp.json()["detail"]


def test_404s_on_a_chunk_with_no_run_context(tmp_path: Path) -> None:
    """A chunk that is not a routine run gets a legible refusal, not an empty list."""
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub, with_run_context=False)
    resp = hub.client.get(f"/api/fleet/chunks/{chunk_id}/garden/proposals")
    assert resp.status_code == 404, resp.text
    assert "no run context" in resp.json()["detail"]


def test_returns_the_routines_open_proposals_via_the_shared_projection(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _seed_finding(hub, "fin_1")
    _seed_proposal(hub, "gprop_1")
    _seed_proposal(hub, "gprop_other", routine_name="other-routine")  # a different routine — not in this bucket

    resp = hub.client.get(f"/api/fleet/chunks/{chunk_id}/garden/proposals")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [row["proposal_id"] for row in body] == ["gprop_1"]
    assert body[0]["closure"] is None


def test_excludes_a_closed_proposal(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _seed_finding(hub, "fin_1")
    _seed_proposal(hub, "gprop_open")
    _seed_proposal(hub, "gprop_closed")
    _pass_proposal(hub, "gprop_closed")

    resp = hub.client.get(f"/api/fleet/chunks/{chunk_id}/garden/proposals")
    assert resp.status_code == 200, resp.text
    assert [row["proposal_id"] for row in resp.json()] == ["gprop_open"]


def test_takes_no_routine_flag(tmp_path: Path) -> None:
    """The route's own signature carries no such parameter — passing one is a no-op,
    never a way to reach another routine's docket."""
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _seed_finding(hub, "fin_1")
    _seed_proposal(hub, "gprop_1")

    resp = hub.client.get(f"/api/fleet/chunks/{chunk_id}/garden/proposals", params={"routine": "other-routine"})
    assert resp.status_code == 200, resp.text
    assert [row["proposal_id"] for row in resp.json()] == ["gprop_1"]
