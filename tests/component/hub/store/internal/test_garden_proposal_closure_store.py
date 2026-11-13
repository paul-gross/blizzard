"""``GardenProposalClosureStore`` and the minting accept's unit (component tier).
The pass, accept-declining-to-mint, and accept-with-mint writes are idempotent per
``proposal_id`` at the store level — the second call of any writes nothing — and
``GardenProposalClosureService.accept`` writes the closure, the item, and its resting chunk
in one unit, the closure first so an already-closed proposal mints nothing a second time.
Each write runs in a unit holding the proposal's key."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import select

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.garden_proposals import (
    GardenProposalClosureKind,
    GardenProposalItemOutcome,
    GardenProposalOrigin,
)
from blizzard.foundation.store import batching as batching_module
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.garden.proposals.closure import (
    GardenProposalClosure,
    GardenProposalClosureService,
    IWriteGardenProposalClosureRepository,
)
from blizzard.hub.domain.garden.proposals.model import GardenProposalAlreadyClosed
from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction
from blizzard.hub.graphs import PACKAGED
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.garden_proposal_closure_store import (
    GardenProposalClosureStore,
    insert_garden_proposal_closure_row,
)
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.store.transactions import HubTransactions
from tests.support.garden_proposal_units import proposal_unit
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    count_queries,
    default_project,
    hub_scoped,
    hub_store_connections,
    stamp,
)

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _seed_proposal(hub: HubHarness, *, proposal_id: str = "gprop_1") -> None:
    with hub.engine.begin() as conn:
        conn.execute(s.scopes.insert().values(stamp(s.scopes, slug="blizzard", description="", created_at=_NOW)))
    store = hub_store_connections(hub.engine)
    FindingStore(store).add(
        "fin_1",
        project_id=default_project(hub.engine),
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
        project_id=default_project(hub.engine),
        origin=GardenProposalOrigin.ROUTINE_RUN,
        routine_name="nightly",
        class_="fix-the-source",
        title="Author a docstring standard",
        body="the case",
        findings=["fin_1"],
        at=_NOW,
    )


def _seed_proposals(hub: HubHarness, proposal_ids: list[str]) -> None:
    with hub.engine.begin() as conn:
        conn.execute(s.scopes.insert().values(stamp(s.scopes, slug="blizzard", description="", created_at=_NOW)))
    store = hub_store_connections(hub.engine)
    findings = FindingStore(store)
    proposals = GardenProposalStore(store)
    for i, proposal_id in enumerate(proposal_ids):
        finding_id = f"fin_{i}"
        findings.add(
            finding_id,
            project_id=default_project(hub.engine),
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
            project_id=default_project(hub.engine),
            origin=GardenProposalOrigin.ROUTINE_RUN,
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
    _in_unit(
        hub, "gprop_1", lambda t: closures.record_pass("gprop_1", reason="r1", closed_by="u1", at=_NOW, transaction=t)
    )
    _in_unit(
        hub,
        "gprop_2",
        lambda t: closures.record_accept_decline("gprop_2", reason=None, closed_by="u1", at=_NOW, transaction=t),
    )

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
        _in_unit(
            hub,
            proposal_id,
            lambda t, p=proposal_id: closures.record_pass(p, reason="r", closed_by="u1", at=_NOW, transaction=t),
        )

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
        _in_unit(
            hub,
            proposal_id,
            lambda t, p=proposal_id: closures.record_pass(p, reason="r", closed_by="u1", at=_NOW, transaction=t),
        )

    one_batch_count = count_queries(hub.engine, lambda: closures.get_many(proposal_ids[:3]))
    two_batch_count = count_queries(hub.engine, lambda: closures.get_many(proposal_ids[:6]))
    three_batch_count = count_queries(hub.engine, lambda: closures.get_many(proposal_ids))

    per_batch_cost = two_batch_count - one_batch_count
    assert per_batch_cost > 0
    assert two_batch_count == one_batch_count + per_batch_cost
    assert three_batch_count == one_batch_count + 2 * per_batch_cost  # the trailing size-1 batch costs the same


def _in_unit(hub: HubHarness, proposal_id: str, write: Callable[[LockedTransaction], bool]) -> bool:
    with proposal_unit(hub_store_connections(hub.engine), proposal_id) as transaction:
        return write(transaction)


def _mint(hub: HubHarness, proposal_id: str, pointer: WorkRef) -> None:
    with hub_store_connections(hub.engine).write("seed_mint") as conn:
        insert_garden_proposal_closure_row(
            conn,
            proposal_id=proposal_id,
            closure=GardenProposalClosureKind.ACCEPTED,
            reason=None,
            closed_by="u1",
            at=_NOW,
            item_outcome=GardenProposalItemOutcome.MINTED,
            pointer=pointer,
        )


def test_find_by_items_round_trips_minted_pointers_and_drops_unminted_ones(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_proposals(hub, ["gprop_1", "gprop_2", "gprop_3", "gprop_4"])
    closures = GardenProposalClosureStore(hub_store_connections(hub.engine))
    _mint(hub, "gprop_1", WorkRef(source="hub", ref="1"))
    _mint(hub, "gprop_2", WorkRef(source="other", ref="1"))
    _in_unit(
        hub, "gprop_3", lambda t: closures.record_pass("gprop_3", reason="r", closed_by="u1", at=_NOW, transaction=t)
    )
    _in_unit(
        hub,
        "gprop_4",
        lambda t: closures.record_accept_decline("gprop_4", reason=None, closed_by="u1", at=_NOW, transaction=t),
    )

    result = closures.find_by_items(
        [WorkRef(source="hub", ref="1"), WorkRef(source="other", ref="1"), WorkRef(source="hub", ref="nope")]
    )

    assert set(result) == {WorkRef(source="hub", ref="1"), WorkRef(source="other", ref="1")}
    assert result[WorkRef(source="hub", ref="1")].proposal_id == "gprop_1"
    assert result[WorkRef(source="other", ref="1")].proposal_id == "gprop_2"
    assert result[WorkRef(source="hub", ref="1")] == closures.find_by_item("hub", "1")


def test_find_by_items_keeps_the_same_ref_under_another_source_distinct(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_proposals(hub, ["gprop_1"])
    _mint(hub, "gprop_1", WorkRef(source="hub", ref="7"))
    closures = GardenProposalClosureStore(hub_store_connections(hub.engine))

    assert closures.find_by_items([WorkRef(source="elsewhere", ref="7")]) == {}


def test_find_by_items_of_no_pointers_is_empty(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert GardenProposalClosureStore(hub_store_connections(hub.engine)).find_by_items([]) == {}


def test_find_by_items_query_cost_scales_with_batch_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    hub = build_hub(tmp_path)
    proposal_ids = [f"gprop_{i}" for i in range(7)]
    _seed_proposals(hub, proposal_ids)
    pointers = [WorkRef(source="hub", ref=str(i)) for i in range(7)]
    for proposal_id, pointer in zip(proposal_ids, pointers, strict=True):
        _mint(hub, proposal_id, pointer)
    closures = GardenProposalClosureStore(hub_store_connections(hub.engine))

    one = count_queries(hub.engine, lambda: closures.find_by_items(pointers[:3]))
    two = count_queries(hub.engine, lambda: closures.find_by_items(pointers[:6]))
    three = count_queries(hub.engine, lambda: closures.find_by_items(pointers))

    per_batch = two - one
    assert per_batch > 0
    assert three == one + 2 * per_batch
    assert len(closures.find_by_items(pointers)) == 7


def test_record_pass_then_get_round_trips(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_proposal(hub)
    closures = GardenProposalClosureStore(hub_store_connections(hub.engine))

    written = _in_unit(
        hub,
        "gprop_1",
        lambda t: closures.record_pass("gprop_1", reason="not worth it", closed_by="u1", at=_NOW, transaction=t),
    )

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

    first = _in_unit(
        hub, "gprop_1", lambda t: closures.record_pass("gprop_1", reason="r1", closed_by="u1", at=_NOW, transaction=t)
    )
    second = _in_unit(
        hub, "gprop_1", lambda t: closures.record_pass("gprop_1", reason="r2", closed_by="u2", at=_NOW, transaction=t)
    )

    assert first is True
    assert second is False
    with hub.engine.connect() as conn:
        rows = conn.execute(
            hub_scoped(select(s.garden_proposal_closures).where(s.garden_proposal_closures.c.proposal_id == "gprop_1"))
        ).all()
    assert len(rows) == 1
    assert rows[0].reason == "r1"


def test_record_accept_decline_round_trips_and_is_idempotent(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_proposal(hub)
    closures = GardenProposalClosureStore(hub_store_connections(hub.engine))

    first = _in_unit(
        hub,
        "gprop_1",
        lambda t: closures.record_accept_decline("gprop_1", reason=None, closed_by="u1", at=_NOW, transaction=t),
    )
    second = _in_unit(
        hub,
        "gprop_1",
        lambda t: closures.record_accept_decline("gprop_1", reason="too late", closed_by="u2", at=_NOW, transaction=t),
    )

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


def test_record_accept_mint_round_trips_naming_the_item_and_is_idempotent(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_proposal(hub)
    closures = GardenProposalClosureStore(hub_store_connections(hub.engine))
    pointer = WorkRef(source="hub", ref="7")

    first = _in_unit(
        hub,
        "gprop_1",
        lambda t: closures.record_accept_mint(
            "gprop_1", pointer=pointer, reason=None, closed_by="u1", at=_NOW, transaction=t
        ),
    )
    second = _in_unit(
        hub,
        "gprop_1",
        lambda t: closures.record_accept_mint(
            "gprop_1", pointer=WorkRef(source="hub", ref="8"), reason="again", closed_by="u2", at=_NOW, transaction=t
        ),
    )

    assert first is True
    assert second is False
    closure = closures.get("gprop_1")
    assert closure is not None
    assert closure.closure is GardenProposalClosureKind.ACCEPTED
    assert closure.item_outcome is GardenProposalItemOutcome.MINTED
    assert closure.source == "hub" and closure.ref == "7"
    assert closure.closed_by == "u1"


class _StaleBeforeTheUnit:
    """The real closure store, except its read before the accept's unit misses a closure that lands before the
    unit opens — the race the unit's insert-if-absent closes."""

    def __init__(self, closures: GardenProposalClosureStore) -> None:
        self._closures = closures

    def get(self, proposal_id: str, *, transaction: Transaction | None = None) -> GardenProposalClosure | None:
        return self._closures.get(proposal_id, transaction=transaction) if transaction is not None else None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._closures, name)


def _closure_service(hub: HubHarness, closures: IWriteGardenProposalClosureRepository) -> GardenProposalClosureService:
    store = hub_store_connections(hub.engine)
    graph = hub.services.graph_mint.ensure_default(PACKAGED.default.doc, definition_yaml=PACKAGED.default.text)
    return GardenProposalClosureService(
        closures=closures,
        items=hub.services.garden_proposal_closure.items,
        default_graph=lambda: graph,
        clock=FixedClock(_NOW),
        transactions=HubTransactions(store),
    )


def _hub_chunk_refs(hub: HubHarness) -> list[str]:
    with hub.engine.connect() as conn:
        rows = conn.execute(hub_scoped(select(s.chunk_work_refs).where(s.chunk_work_refs.c.source == "hub"))).all()
    return [row.ref for row in rows]


def _closure_rows(hub: HubHarness, proposal_id: str) -> int:
    with hub.engine.connect() as conn:
        rows = conn.execute(
            hub_scoped(
                select(s.garden_proposal_closures).where(s.garden_proposal_closures.c.proposal_id == proposal_id)
            )
        ).all()
    return len(rows)


def test_a_minting_accept_writes_the_closure_item_and_chunk_together(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_proposal(hub)
    store = hub_store_connections(hub.engine)
    proposal = GardenProposalStore(store).get("gprop_1")
    assert proposal is not None
    service = _closure_service(hub, GardenProposalClosureStore(store))

    accepted = service.accept(proposal, reason=None, by="u1", body=None, mint=True, findings=[])

    closure = GardenProposalClosureStore(store).get("gprop_1")
    assert closure is not None
    assert closure == accepted.closure
    assert closure.closure is GardenProposalClosureKind.ACCEPTED
    assert closure.item_outcome is GardenProposalItemOutcome.MINTED
    assert closure.source == "hub" and closure.ref is not None
    item = WorkItemStore(store).get("hub", closure.ref)
    assert item is not None and item.title == proposal.title
    assert accepted.chunk_id is not None
    assert _hub_chunk_refs(hub) == [closure.ref]
    assert _closure_rows(hub, "gprop_1") == 1


def test_a_minting_accept_of_a_proposal_closed_before_its_unit_raises_and_writes_nothing_further(
    tmp_path: Path,
) -> None:
    """A pass lands after the accept's own read and before its unit: the closure insert finds it, so the unit
    writes no item, no chunk, and no second closure, and the accept refuses naming the closure that won."""
    hub = build_hub(tmp_path)
    _seed_proposal(hub)
    store = hub_store_connections(hub.engine)
    closures = GardenProposalClosureStore(store)
    proposal = GardenProposalStore(store).get("gprop_1")
    assert proposal is not None
    _in_unit(
        hub,
        "gprop_1",
        lambda t: closures.record_pass("gprop_1", reason="already passed", closed_by="u0", at=_NOW, transaction=t),
    )
    service = _closure_service(hub, cast(IWriteGardenProposalClosureRepository, _StaleBeforeTheUnit(closures)))

    with pytest.raises(GardenProposalAlreadyClosed) as refused:
        service.accept(proposal, reason=None, by="u1", body=None, mint=True, findings=[])

    assert refused.value.closure.closure is GardenProposalClosureKind.PASSED
    assert WorkItemStore(store).list("hub") == []
    assert _hub_chunk_refs(hub) == []
    assert _closure_rows(hub, "gprop_1") == 1
