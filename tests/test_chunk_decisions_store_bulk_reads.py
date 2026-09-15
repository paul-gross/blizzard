"""``ChunkDecisionsStore``'s bulk and SQL-filtered reads (component tier).

Proves ``dockets_for_chunks`` matches ``decision_for_chunk`` exactly — including an empty
list for a chunk with no proposals and exclusion of judged proposals — across a lowered
``BATCH_SIZE`` boundary; and that ``list_open_decisions``/``decision_for_chunk`` filter in
SQL and hydrate in bounded batches, with a flat query count as fleet/history size grows."""

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


def _record_decision(
    store: ChunkStores, chunk_id: str, decision_id: str, proposals: list[WorkItemProposalRow], *, at: datetime = _T0
) -> None:
    store.decisions.record_decision(
        decision_id=decision_id,
        chunk_id=chunk_id,
        node_id="nd_1",
        node_name="n",
        epoch=1,
        choices=[DecisionChoice(name="ok", description="d")],
        at=at,
        artifacts=[],
        proposals=proposals,
    )


def _resolve(store: ChunkStores, decision_id: str, *, choice: str = "pass", by: str = "op") -> None:
    store.decisions.record_decision_resolution(decision_id, choice=choice, resolved_by=by, at=_T0)


def _transition(store: ChunkStores, chunk_id: str, decision_id: str, *, transition_id: str) -> None:
    """Closes ``decision_id`` — the movement fact `decision_for_chunk`'s
    ``_not_closed_clause`` reads to drop it from a chunk's live decision."""
    store.movement.record_transition(
        transition_id=transition_id,
        chunk_id=chunk_id,
        from_node_id="nd_1",
        to_node_id="nd_2",
        choice_name="pass",
        epoch=1,
        runner_id="r1",
        at=_T0,
        artifacts=[],
        proposals=[],
        decision_id=decision_id,
    )


def test_dockets_for_chunks_matches_decision_for_chunk_and_includes_a_zero_proposal_chunk(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    _mint(store, "ch_with_proposals")
    _mint(store, "ch_no_proposals")
    _record_decision(
        store,
        "ch_with_proposals",
        "dec_1",
        [
            _proposal("ch_with_proposals", "wip_keep", ordinal=0),
            _proposal("ch_with_proposals", "wip_strike", ordinal=1),
        ],
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


def test_dockets_for_chunks_does_not_duplicate_an_id_repeated_across_two_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 2)
    store, _ = _store(tmp_path)
    _mint(store, "ch_a")
    _mint(store, "ch_b")
    _record_decision(store, "ch_a", "dec_a", [_proposal("ch_a", "wip_a")])

    # BATCH_SIZE=2 over ["ch_a", "ch_b", "ch_a"] splits into ["ch_a","ch_b"] and ["ch_a"] —
    # ch_a lands in two batches if the ids aren't deduplicated before batching.
    result = store.decisions.dockets_for_chunks(["ch_a", "ch_b", "ch_a"])

    assert set(result) == {"ch_a", "ch_b"}
    assert [e.proposal.proposal_id for e in result["ch_a"]] == ["wip_a"]


def test_dockets_for_chunks_query_cost_scales_linearly_with_batch_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each batch's own judged/strike reads cost the same regardless of trailing size —
    the per-batch cost is constant, so the total never outruns the batch count."""
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


def test_list_open_decisions_query_count_is_independent_of_fleet_size(tmp_path: Path) -> None:
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small, small_engine = _store(tmp_path / "small")
    large, large_engine = _store(tmp_path / "large")
    for i in range(3):
        _mint(small, f"ch_{i}")
        _record_decision(small, f"ch_{i}", f"dec_{i}", [_proposal(f"ch_{i}", f"wip_{i}")])
    for i in range(9):
        _mint(large, f"ch_{i}")
        _record_decision(large, f"ch_{i}", f"dec_{i}", [_proposal(f"ch_{i}", f"wip_{i}")])

    small_count = count_queries(small_engine, lambda: small.decisions.list_open_decisions())
    large_count = count_queries(large_engine, lambda: large.decisions.list_open_decisions())

    assert len(small.decisions.list_open_decisions()) == 3
    assert len(large.decisions.list_open_decisions()) == 9
    assert small_count == large_count


def test_decision_for_chunk_query_count_is_independent_of_that_chunks_history_length(tmp_path: Path) -> None:
    """A chunk's own closed history shouldn't cost more queries the longer it gets — the
    ``NOT EXISTS`` filter drops every closed decision in SQL, before hydration."""
    (tmp_path / "short").mkdir()
    (tmp_path / "long").mkdir()
    short, short_engine = _store(tmp_path / "short")
    long_, long_engine = _store(tmp_path / "long")
    _mint(short, "ch_1")
    for i in range(3):
        _record_decision(short, "ch_1", f"dec_closed_{i}", [])
        _resolve(short, f"dec_closed_{i}")
        _transition(short, "ch_1", f"dec_closed_{i}", transition_id=f"tr_{i}")
    _record_decision(short, "ch_1", "dec_live", [])
    _mint(long_, "ch_1")
    for i in range(9):
        _record_decision(long_, "ch_1", f"dec_closed_{i}", [])
        _resolve(long_, f"dec_closed_{i}")
        _transition(long_, "ch_1", f"dec_closed_{i}", transition_id=f"tr_{i}")
    _record_decision(long_, "ch_1", "dec_live", [])

    short_count = count_queries(short_engine, lambda: short.decisions.decision_for_chunk("ch_1"))
    long_count = count_queries(long_engine, lambda: long_.decisions.decision_for_chunk("ch_1"))

    short_result = short.decisions.decision_for_chunk("ch_1")
    long_result = long_.decisions.decision_for_chunk("ch_1")
    assert short_result is not None and short_result.decision_id == "dec_live"
    assert long_result is not None and long_result.decision_id == "dec_live"
    assert short_count == long_count


def test_list_open_decisions_and_decision_for_chunk_across_the_four_decision_shapes(tmp_path: Path) -> None:
    """A resolved decision, an open one, a transitioned chunk, and a chunk with several
    decisions across its history — the shapes both SQL-filtered reads must still
    resolve correctly, over a real migrated store."""
    store, _ = _store(tmp_path)
    _mint(store, "ch_resolved")
    _mint(store, "ch_open")
    _mint(store, "ch_transitioned")
    _mint(store, "ch_history")

    _record_decision(store, "ch_resolved", "dec_resolved", [])
    _resolve(store, "dec_resolved")

    _record_decision(store, "ch_open", "dec_open", [])

    _record_decision(store, "ch_transitioned", "dec_transitioned", [])
    _resolve(store, "dec_transitioned")
    _transition(store, "ch_transitioned", "dec_transitioned", transition_id="tr_transitioned")

    _record_decision(store, "ch_history", "dec_history_old", [])
    _resolve(store, "dec_history_old")
    _transition(store, "ch_history", "dec_history_old", transition_id="tr_history")
    _record_decision(store, "ch_history", "dec_history_new", [])

    open_ids = {d.decision_id for d in store.decisions.list_open_decisions()}
    assert open_ids == {"dec_open", "dec_history_new"}

    resolved = store.decisions.decision_for_chunk("ch_resolved")
    assert resolved is not None
    assert resolved.decision_id == "dec_resolved"
    assert resolved.resolved_choice == "pass"
    assert not resolved.transitioned

    open_decision = store.decisions.decision_for_chunk("ch_open")
    assert open_decision is not None
    assert open_decision.decision_id == "dec_open"
    assert not open_decision.resolved

    assert store.decisions.decision_for_chunk("ch_transitioned") is None

    history = store.decisions.decision_for_chunk("ch_history")
    assert history is not None
    assert history.decision_id == "dec_history_new"
