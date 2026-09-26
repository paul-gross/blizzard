"""``GardenProposalClosureStore`` and ``WorkItemStore.accept_create`` (component tier,
blizzard#395). The pass and accept-declining-to-mint writes are idempotent per
``proposal_id`` at the store level — the second call of either writes nothing — and
accept-with-mint atomically writes the closure, the item, and its resting chunk
together, checked first so an already-closed proposal mints nothing a second time. The
``tests/test_work_item_materialization_store.py`` shape."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import select

from blizzard.hub.domain.garden_proposal_closure import GardenProposalClosureKind, GardenProposalItemOutcome
from blizzard.hub.domain.work import IWriteWorkItemRepository, WorkItemAuthor, WorkRef, mint_chunk
from blizzard.hub.graphs import PACKAGED
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import batching as batching_module
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.garden_proposal_closure_store import GardenProposalClosureStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import build_hub, count_queries, hub_store_connections

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _seed_proposal(hub, *, proposal_id: str = "gprop_1") -> None:  # type: ignore[no-untyped-def]
    with hub.engine.begin() as conn:
        conn.execute(s.scopes.insert().values(slug="blizzard", description="", created_at=_NOW))
    store = hub_store_connections(hub.engine)
    FindingStore(store).add(
        "fin_1",
        routine_name="nightly",
        scope_slug="blizzard",
        class_="stale-docstring",
        locus="a.py:1",
        summary="s1",
        introduced=None,
        at=_NOW,
    )
    GardenProposalStore(store).create(
        proposal_id,
        routine_name="nightly",
        class_="fix-the-source",
        title="Author a docstring standard",
        body="the case",
        findings=["fin_1"],
        at=_NOW,
    )


def _seed_proposals(hub, proposal_ids: list[str]) -> None:  # type: ignore[no-untyped-def]
    with hub.engine.begin() as conn:
        conn.execute(s.scopes.insert().values(slug="blizzard", description="", created_at=_NOW))
    store = hub_store_connections(hub.engine)
    findings = FindingStore(store)
    proposals = GardenProposalStore(store)
    for i, proposal_id in enumerate(proposal_ids):
        finding_id = f"fin_{i}"
        findings.add(
            finding_id,
            routine_name="nightly",
            scope_slug="blizzard",
            class_="stale-docstring",
            locus="a.py:1",
            summary="s1",
            introduced=None,
            at=_NOW,
        )
        proposals.create(
            proposal_id,
            routine_name="nightly",
            class_="fix-the-source",
            title="Author a docstring standard",
            body="the case",
            findings=[finding_id],
            at=_NOW,
        )


def test_get_many_matches_get_across_two_closed_proposals_and_skips_an_unclosed_one(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_proposals(hub, ["gprop_1", "gprop_2", "gprop_3"])
    closures = GardenProposalClosureStore(hub_store_connections(hub.engine))
    closures.record_pass("gprop_1", reason="r1", closed_by="u1", at=_NOW)
    closures.record_accept_decline("gprop_2", reason=None, closed_by="u1", at=_NOW)

    result = closures.get_many(["gprop_1", "gprop_2", "gprop_3", "gprop_ghost"])

    assert set(result) == {"gprop_1", "gprop_2"}
    assert result["gprop_1"].closure is GardenProposalClosureKind.PASSED
    assert result["gprop_2"].closure is GardenProposalClosureKind.ACCEPTED


def test_get_many_of_no_ids_is_empty(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert GardenProposalClosureStore(hub_store_connections(hub.engine)).get_many([]) == {}


def test_get_many_matches_across_a_batch_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    hub = build_hub(tmp_path)
    proposal_ids = [f"gprop_{i}" for i in range(7)]
    _seed_proposals(hub, proposal_ids)
    closures = GardenProposalClosureStore(hub_store_connections(hub.engine))
    for proposal_id in proposal_ids:
        closures.record_pass(proposal_id, reason="r", closed_by="u1", at=_NOW)

    result = closures.get_many(proposal_ids)

    assert set(result) == set(proposal_ids)
    for proposal_id in proposal_ids:
        assert result[proposal_id].closure is GardenProposalClosureKind.PASSED


def test_get_many_query_cost_scales_linearly_with_batch_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each batch's own read costs the same regardless of trailing size — the per-batch
    cost is constant, so the total grows with batch count, not id count."""
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    hub = build_hub(tmp_path)
    proposal_ids = [f"gprop_{i}" for i in range(7)]
    _seed_proposals(hub, proposal_ids)
    closures = GardenProposalClosureStore(hub_store_connections(hub.engine))
    for proposal_id in proposal_ids:
        closures.record_pass(proposal_id, reason="r", closed_by="u1", at=_NOW)

    one_batch_count = count_queries(hub.engine, lambda: closures.get_many(proposal_ids[:3]))
    two_batch_count = count_queries(hub.engine, lambda: closures.get_many(proposal_ids[:6]))
    three_batch_count = count_queries(hub.engine, lambda: closures.get_many(proposal_ids))

    per_batch_cost = two_batch_count - one_batch_count
    assert per_batch_cost > 0
    assert two_batch_count == one_batch_count + per_batch_cost
    assert three_batch_count == one_batch_count + 2 * per_batch_cost  # the trailing size-1 batch costs the same


def test_record_pass_then_get_round_trips(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_proposal(hub)
    closures = GardenProposalClosureStore(hub_store_connections(hub.engine))

    written = closures.record_pass("gprop_1", reason="not worth it", closed_by="u1", at=_NOW)

    assert written is True
    closure = closures.get("gprop_1")
    assert closure is not None
    assert closure.closure is GardenProposalClosureKind.PASSED
    assert closure.reason == "not worth it"
    assert closure.closed_by == "u1"
    assert closure.item_outcome is None
    assert closure.source is None and closure.ref is None


def test_record_pass_returns_false_on_a_second_call(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_proposal(hub)
    closures = GardenProposalClosureStore(hub_store_connections(hub.engine))

    first = closures.record_pass("gprop_1", reason="r1", closed_by="u1", at=_NOW)
    second = closures.record_pass("gprop_1", reason="r2", closed_by="u2", at=_NOW)

    assert first is True
    assert second is False
    with hub.engine.connect() as conn:
        rows = conn.execute(
            select(s.garden_proposal_closures).where(s.garden_proposal_closures.c.proposal_id == "gprop_1")
        ).all()
    assert len(rows) == 1
    assert rows[0].reason == "r1"


def test_record_accept_decline_round_trips_and_is_idempotent(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_proposal(hub)
    closures = GardenProposalClosureStore(hub_store_connections(hub.engine))

    first = closures.record_accept_decline("gprop_1", reason=None, closed_by="u1", at=_NOW)
    second = closures.record_accept_decline("gprop_1", reason="too late", closed_by="u2", at=_NOW)

    assert first is True
    assert second is False
    closure = closures.get("gprop_1")
    assert closure is not None
    assert closure.closure is GardenProposalClosureKind.ACCEPTED
    assert closure.item_outcome is GardenProposalItemOutcome.DECLINED
    assert closure.source is None and closure.ref is None


def test_get_unknown_proposal_is_none(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert GardenProposalClosureStore(hub_store_connections(hub.engine)).get("gprop_ghost") is None


def test_accept_create_mints_the_closure_item_and_chunk_atomically_and_is_idempotent(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_proposal(hub)
    graph = hub.services.graph_mint.ensure_default(PACKAGED.default.doc, definition_yaml=PACKAGED.default.text)
    store = hub_store_connections(hub.engine)
    items = cast(IWriteWorkItemRepository, WorkItemStore(store))
    author = WorkItemAuthor.user("u1")
    pointer = WorkRef(source="hub", ref=items.allocate_ref("hub"))
    chunk = mint_chunk([pointer], graph_id=graph.graph_id, at=_NOW)

    first = items.accept_create(
        proposal_id="gprop_1",
        pointer=pointer,
        title="t",
        body="b",
        author=author,
        at=_NOW,
        chunk=chunk,
        reason=None,
        closed_by="u1",
    )
    assert first is not None
    assert items.get("hub", pointer.ref) is not None
    closure = GardenProposalClosureStore(store).get("gprop_1")
    assert closure is not None
    assert closure.closure is GardenProposalClosureKind.ACCEPTED
    assert closure.item_outcome is GardenProposalItemOutcome.MINTED
    assert closure.source == "hub" and closure.ref == pointer.ref

    second = items.accept_create(
        proposal_id="gprop_1",
        pointer=pointer,
        title="t",
        body="b",
        author=author,
        at=_NOW,
        chunk=chunk,
        reason=None,
        closed_by="u1",
    )
    assert second is None  # already closed — nothing minted a second time

    with hub.engine.connect() as conn:
        rows = conn.execute(
            select(s.garden_proposal_closures).where(s.garden_proposal_closures.c.proposal_id == "gprop_1")
        ).all()
    assert len(rows) == 1


def test_accept_create_writes_nothing_when_the_proposal_is_already_closed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_proposal(hub)
    graph = hub.services.graph_mint.ensure_default(PACKAGED.default.doc, definition_yaml=PACKAGED.default.text)
    store = hub_store_connections(hub.engine)
    closures = GardenProposalClosureStore(store)
    closures.record_pass("gprop_1", reason="already passed", closed_by="u0", at=_NOW)
    items = cast(IWriteWorkItemRepository, WorkItemStore(store))
    pointer = WorkRef(source="hub", ref=items.allocate_ref("hub"))
    chunk = mint_chunk([pointer], graph_id=graph.graph_id, at=_NOW)

    result = items.accept_create(
        proposal_id="gprop_1",
        pointer=pointer,
        title="t",
        body="b",
        author=WorkItemAuthor.user("u1"),
        at=_NOW,
        chunk=chunk,
        reason=None,
        closed_by="u1",
    )

    assert result is None
    assert items.get("hub", pointer.ref) is None
