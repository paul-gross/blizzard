"""``ChunkDecisionsStore.dockets_for_chunks`` — ``_pending_proposals``'s batched sibling
(component tier).

Proves the batch read returns, for every requested chunk, exactly what
``decision_for_chunk`` would carry as its own docket — including a chunk with no
proposals at all, mapped to an empty list rather than an absent key — that a judged
proposal is excluded the same way, that it stays correct across a lowered
``BATCH_SIZE`` boundary, and that the global judged-set read costs one statement total
rather than one per batch."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunks.stores import ChunkStores
from blizzard.hub.domain.proposals import WorkItemProposalRow
from blizzard.hub.domain.work import Chunk, DecisionChoice, WorkItemMaterializationOutcome
from blizzard.hub.store.internal import batching as batching_module
from blizzard.hub.store.internal.chunk_rows import insert_materialization_row
from tests.support import chunk_stores, count_queries, migrate_to, seed_graph

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _store(tmp_path: Path) -> tuple[ChunkStores, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
    return chunk_stores(engine, FixedClock(_T0)), engine


def _mint(store: ChunkStores, chunk_id: str) -> None:
    store.record.mint(Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=_T0))


def _proposal(chunk_id: str, proposal_id: str, *, ordinal: int = 0) -> WorkItemProposalRow:
    return WorkItemProposalRow(
        proposal_id=proposal_id,
        chunk_id=chunk_id,
        node_id="nd_1",
        node_name="n",
        epoch=1,
        ordinal=ordinal,
        kind="create",
        data="{}",
        runner_id="r1",
    )


def _record_decision(store: ChunkStores, chunk_id: str, decision_id: str, proposals: list[WorkItemProposalRow]) -> None:
    store.decisions.record_decision(
        decision_id=decision_id,
        chunk_id=chunk_id,
        node_id="nd_1",
        node_name="n",
        epoch=1,
        choices=[DecisionChoice(name="ok", description="d")],
        at=_T0,
        artifacts=[],
        proposals=proposals,
    )


def test_dockets_for_chunks_matches_decision_for_chunk_and_includes_a_zero_proposal_chunk(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    _mint(store, "ch_with_proposals")
    _mint(store, "ch_no_proposals")
    _record_decision(
        store,
        "ch_with_proposals",
        "dec_1",
        [_proposal("ch_with_proposals", "wip_keep", ordinal=0), _proposal("ch_with_proposals", "wip_strike", ordinal=1)],
    )
    store.decisions.record_decision_resolution("dec_1", choice="ok", resolved_by="op", at=_T0, struck=["wip_strike"])

    expected = store.decisions.decision_for_chunk("ch_with_proposals")
    assert expected is not None

    result = store.decisions.dockets_for_chunks(["ch_with_proposals", "ch_no_proposals", "ch_never_minted"])

    assert set(result) == {"ch_with_proposals", "ch_no_proposals", "ch_never_minted"}
    assert result["ch_with_proposals"] == expected.docket
    assert {e.proposal.proposal_id for e in result["ch_with_proposals"]} == {"wip_keep", "wip_strike"}
    assert {e.struck for e in result["ch_with_proposals"]} == {False, True}
    assert result["ch_no_proposals"] == []
    assert result["ch_never_minted"] == []


def test_dockets_for_chunks_excludes_a_judged_proposal_the_same_as_decision_for_chunk(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _mint(store, "ch_1")
    _record_decision(store, "ch_1", "dec_1", [_proposal("ch_1", "wip_judged"), _proposal("ch_1", "wip_open")])
    with engine.begin() as conn:
        insert_materialization_row(
            conn,
            proposal_id="wip_judged",
            outcome=WorkItemMaterializationOutcome.CREATED,
            pointer=None,
            reason=None,
            at=_T0,
        )

    expected = store.decisions.decision_for_chunk("ch_1")
    assert expected is not None

    result = store.decisions.dockets_for_chunks(["ch_1"])

    assert result["ch_1"] == expected.docket
    assert {e.proposal.proposal_id for e in result["ch_1"]} == {"wip_open"}


def test_dockets_for_chunks_of_no_ids_is_empty(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)

    assert store.decisions.dockets_for_chunks([]) == {}


def test_dockets_for_chunks_matches_decision_for_chunk_across_a_batch_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    store, _ = _store(tmp_path)
    ids = [f"ch_batch_{i}" for i in range(7)]  # 3 batches of size 3, 3, 1 under the lowered cap
    for chunk_id in ids:
        _mint(store, chunk_id)
        _record_decision(store, chunk_id, f"dec_{chunk_id}", [_proposal(chunk_id, f"wip_{chunk_id}")])

    result = store.decisions.dockets_for_chunks(ids)

    assert set(result) == set(ids)
    for chunk_id in ids:
        expected = store.decisions.decision_for_chunk(chunk_id)
        assert expected is not None
        assert result[chunk_id] == expected.docket


def test_dockets_for_chunks_reads_the_judged_set_once_total_not_once_per_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    store, engine = _store(tmp_path)
    ids = [f"ch_batch_{i}" for i in range(7)]
    for chunk_id in ids:
        _mint(store, chunk_id)
        _record_decision(store, chunk_id, f"dec_{chunk_id}", [_proposal(chunk_id, f"wip_{chunk_id}")])

    one_batch_count = count_queries(engine, lambda: store.decisions.dockets_for_chunks(ids[:3]))
    two_batch_count = count_queries(engine, lambda: store.decisions.dockets_for_chunks(ids[:6]))
    three_batch_count = count_queries(engine, lambda: store.decisions.dockets_for_chunks(ids))

    per_batch_cost = two_batch_count - one_batch_count
    assert per_batch_cost > 0
    assert two_batch_count == one_batch_count + per_batch_cost
    assert three_batch_count == one_batch_count + 2 * per_batch_cost  # the trailing size-1 batch costs the same
