"""``IReadChunkEventsRepository.activity_facts_since`` / ``IReadRunnerRegistry.list_pause_facts_since``
(component tier) — the activity feed's bounded per-source reads (AC4).

One case per ``ChunkChangeCause`` member mapped to a fact table, plus the ``since``
window and the per-source ``limit`` bound, against a real store."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import get_args

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.hub_event_types import ActivityChunkChangeCause, ChunkChangeCause
from blizzard.foundation.migration_source import MigrationSource
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.chunk.model import (
    OWN_OCCURRENCE_RELEASE_CAUSES,
    ActivityEntry,
    DecisionChoice,
    RouteReleaseCause,
    decide_close_enqueue,
)
from blizzard.hub.domain.chunk.ports.events import IReadChunkEventsRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.queue import BACKLOG_RANKING_LOCK
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry, RunnerAddition
from blizzard.hub.domain.runners.route import Route
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_events_store import EVENT_SOURCES
from blizzard.hub.store.internal.chunk_rows import record_deleted_row, record_grouped_row_conn
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.transactions import HubTransactions
from tests.support.scaffolding import (
    chunk_stores,
    chunk_unit,
    hub_scoped,
    hub_store_connections,
    migrate_to,
    migrated_hub_at,
    seed_chunk,
    seed_graph,
    seed_promote,
)

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _store(tmp_path: Path) -> tuple[ChunkStores, sa.Engine]:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        seed_graph(conn, "gr_2", at=_T0)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_T0)
    return chunk_stores(engine, FixedClock(_T0)), engine


def _seed_second_chunk(engine: sa.Engine, chunk_id: str) -> None:
    """Seed a second chunk (e.g. a group survivor) directly — no store method mints a
    bare ``chunks`` row with no work refs."""
    with engine.begin() as conn:
        seed_chunk(conn, chunk_id, graph_id="gr_1", at=_T0)


def _row_for(store: ChunkStores, cause: str, *, since: datetime = _T0, limit: int = 50) -> ActivityEntry:
    rows = [r for r in store.events.activity_facts_since(since, limit=limit) if r.cause == cause]
    assert len(rows) == 1, f"expected exactly one {cause!r} row, got {rows}"
    return rows[0]


# --- seeders writing through the store's own writers ----------------------------------------------------------


def _seed_claim(store: ChunkStores, engine: sa.Engine) -> None:
    with chunk_unit(hub_store_connections(engine), "ch_1") as tx:
        store.route.record_route(
            Route(chunk_id="ch_1", runner_id="runner-a", workspace_id="ws-a", environment_ids=[], created_at=_at(1)),
            token_hash="deadbeef",
            at=_at(1),
            transaction=tx,
        )


def _seed_transition(store: ChunkStores, engine: sa.Engine, *, runner_id: str, choice_name: str | None) -> None:
    with chunk_unit(hub_store_connections(engine), "ch_1") as tx:
        store.movement.record_transition(
            transition_id="tr_1",
            chunk_id="ch_1",
            from_node_id=None,
            to_node_id="nd_b",
            choice_name=choice_name,
            epoch=1,
            runner_id=runner_id,
            at=_at(1),
            artifacts=[],
            admission=EpochAdmission.AT_OR_ABOVE,
            terminal=False,
            close_decision=decide_close_enqueue([], lambda _: True),
            transaction=tx,
        )


def _seed_migration(store: ChunkStores, engine: sa.Engine) -> None:
    with chunk_unit(hub_store_connections(engine), "ch_1") as tx:
        store.movement.record_migration(
            "ch_1",
            from_node_id=None,
            from_graph_id="gr_1",
            to_graph_id="gr_2",
            landed_node_id=None,
            choice_name="reroute",
            model=None,
            epoch=1,
            at=_at(1),
            artifacts=[],
            source=MigrationSource.AUTHORED_EDGE,
            admission=EpochAdmission.AT_OR_ABOVE,
            terminal=False,
            close_decision=decide_close_enqueue([], lambda _: True),
            transaction=tx,
        )


def _seed_decision(store: ChunkStores, engine: sa.Engine) -> None:
    with chunk_unit(hub_store_connections(engine), "ch_1") as tx:
        store.decisions.record_decision(
            imposed_by_runner_id=None,
            decision_id="dec_1",
            chunk_id="ch_1",
            node_id="nd_gate",
            node_name="gate",
            epoch=1,
            choices=[DecisionChoice(name="go", description="go")],
            at=_at(1),
            artifacts=[],
            admission=EpochAdmission.AT_OR_ABOVE,
            terminal=False,
            close_decision=decide_close_enqueue([], lambda _: True),
            transaction=tx,
        )


def _seed_decision_resolution(store: ChunkStores, engine: sa.Engine) -> None:
    _seed_decision(store, engine)
    with chunk_unit(hub_store_connections(engine), "ch_1") as unit:
        store.decisions.record_decision_resolution(
            "dec_1", choice="go", resolved_by="alice", at=_at(2), transaction=unit
        )


def _seed_question(store: ChunkStores, engine: sa.Engine) -> None:
    with chunk_unit(hub_store_connections(engine), "ch_1") as tx:
        store.questions.record_question(
            question_id="qn_1",
            chunk_id="ch_1",
            node_id="nd_a",
            session_id=None,
            runner_id="runner-a",
            epoch=1,
            question="continue?",
            options=[],
            asked_at=_at(1),
            admission=EpochAdmission.AT_OR_ABOVE,
            terminal=False,
            transaction=tx,
        )


def _seed_question_answer(store: ChunkStores, engine: sa.Engine) -> None:
    _seed_question(store, engine)
    with chunk_unit(hub_store_connections(engine), "ch_1") as unit:
        store.questions.answer_question("qn_1", answer="yes", answered_by="alice", at=_at(2), transaction=unit)


def _seed_escalation(store: ChunkStores, engine: sa.Engine) -> None:
    with chunk_unit(hub_store_connections(engine), "ch_1") as tx:
        store.escalations.record_escalation(
            "ch_1",
            epoch=1,
            takeover_command="cd x && resume",
            at=_at(1),
            admission=EpochAdmission.AT_OR_ABOVE,
            terminal=False,
            cause=None,
            detail=None,
            transaction=tx,
        )


def _seed_requeue(store: ChunkStores, engine: sa.Engine) -> None:
    with chunk_unit(hub_store_connections(engine), "ch_1") as tx:
        store.movement.record_requeue("ch_1", at=_at(1), transaction=tx)


def _seed_release(store: ChunkStores, engine: sa.Engine) -> None:
    with chunk_unit(hub_store_connections(engine), "ch_1") as tx:
        store.route.record_route_released("ch_1", at=_at(1), cause=RouteReleaseCause.DETACH, transaction=tx)


def _seed_restart(store: ChunkStores, engine: sa.Engine) -> None:
    with chunk_unit(hub_store_connections(engine), "ch_1") as tx:
        store.movement.record_restart(
            "ch_1",
            from_node_id=None,
            to_node_id="nd_build",
            by="alice",
            at=_at(1),
            waiting=(),
            transaction=tx,
        )


def _seed_stop(store: ChunkStores, engine: sa.Engine) -> None:
    with chunk_unit(hub_store_connections(engine), "ch_1") as tx:
        store.lifecycle.record_stop("ch_1", by="alice", at=_T0, waiting=(), transaction=tx)


def _seed_completion(store: ChunkStores, engine: sa.Engine) -> None:
    with chunk_unit(hub_store_connections(engine), "ch_1") as tx:
        store.lifecycle.record_completion(
            "ch_1",
            by="alice",
            at=_T0,
            close_decision=decide_close_enqueue([], lambda _: True),
            waiting=(),
            transaction=tx,
        )


def _seed_demotion(store: ChunkStores, engine: sa.Engine) -> None:
    with HubTransactions(hub_store_connections(engine)).begin() as transaction:
        locked = transaction.lock_set(chunks=["ch_1"], keys=[BACKLOG_RANKING_LOCK])
        store.queue.record_demote_with_position("ch_1", position=1.0, at=_at(1), transaction=locked)


# --- one case per mapped ChunkChangeCause member -----------------------------


def test_minted_reads_off_chunks_minted_at(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    row = _row_for(store, "minted")
    assert row.type == "chunk-changed"
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key == "chunks:ch_1"
    assert row.at == _T0


def test_promoted_reads_off_chunk_promoted(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    seed_promote(store, engine, "ch_1", at=_at(1))
    row = _row_for(store, "promoted")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("chunk_promoted:")
    assert row.at == _at(1)


def test_grouped_reads_off_chunk_grouped(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_second_chunk(engine, "ch_2")
    with hub_store_connections(engine).write("seed") as conn:
        record_grouped_row_conn(conn, "ch_1", grouped_into="ch_2", at=_at(1))
    row = _row_for(store, "grouped")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("chunk_grouped:")


def test_claimed_reads_off_route_created(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_claim(store, engine)
    row = _row_for(store, "claimed")
    assert row.chunk_id == "ch_1"
    assert row.runner_id == "runner-a"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("route_created:")


def test_node_completed_reads_off_transitions_authored_by_a_runner(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_transition(store, engine, runner_id="runner-a", choice_name="approve")
    row = _row_for(store, "node-completed")
    assert row.chunk_id == "ch_1"
    assert row.runner_id == "runner-a"
    assert row.graph_id == "gr_1"
    assert row.key == "transitions:tr_1"


def test_hub_advanced_reads_off_transitions_authored_by_the_hub_coordinator(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_transition(store, engine, runner_id="hub", choice_name=None)
    row = _row_for(store, "hub-advanced")
    assert row.chunk_id == "ch_1"
    assert row.runner_id == "hub"
    assert row.graph_id == "gr_1"
    assert row.key == "transitions:tr_1"


def test_migrated_reads_off_chunk_migrations(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_migration(store, engine)
    row = _row_for(store, "migrated")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_2"  # the migration's target graph, not the one migrated out of
    assert row.key.startswith("chunk_migrations:")


def test_decision_submitted_reads_off_decisions(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_decision(store, engine)
    row = _row_for(store, "decision-submitted")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key == "decisions:dec_1"


def test_decision_resolved_reads_off_decision_resolutions(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_decision_resolution(store, engine)
    row = _row_for(store, "decision-resolved")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key == "decision_resolutions:dec_1"


def test_question_asked_reads_off_questions(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_question(store, engine)
    row = _row_for(store, "question-asked")
    assert row.chunk_id == "ch_1"
    assert row.runner_id == "runner-a"
    assert row.graph_id == "gr_1"
    assert row.key == "questions:qn_1"


def test_question_answered_reads_off_question_answers(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_question_answer(store, engine)
    row = _row_for(store, "question-answered")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key == "question_answers:qn_1"


def test_escalated_reads_off_escalations(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_escalation(store, engine)
    row = _row_for(store, "escalated")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("escalations:")


def test_requeued_reads_off_requeues(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_requeue(store, engine)
    row = _row_for(store, "requeued")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("requeues:")


def test_detached_reads_off_route_released(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_release(store, engine)
    row = _row_for(store, "detached")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("route_released:")


def _route_and_release(
    store: ChunkStores,
    engine: sa.Engine,
    *,
    runner_id: str,
    at: int,
    cause: RouteReleaseCause,
    reason: str | None = None,
) -> None:
    route = Route(chunk_id="ch_1", runner_id=runner_id, workspace_id="w1", environment_ids=[], created_at=_at(at))
    with chunk_unit(hub_store_connections(engine), "ch_1") as tx:
        store.route.record_route(route, token_hash="h", at=_at(at), transaction=tx)
        store.route.record_route_released("ch_1", at=_at(at + 1), cause=cause, reason=reason, transaction=tx)


def test_released_reads_off_a_release_row_naming_its_holder_and_reason(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _route_and_release(store, engine, runner_id="rn_first", at=1, cause=RouteReleaseCause.DETACH)
    _route_and_release(store, engine, runner_id="rn_second", at=3, cause=RouteReleaseCause.RELEASE, reason="throttled")
    row = _row_for(store, "released")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.runner_id == "rn_second"
    assert row.reason == "throttled"
    assert row.key.startswith("route_released:")


@pytest.mark.parametrize("cause", [None, RouteReleaseCause.DETACH, RouteReleaseCause.RETIREMENT])
def test_a_detach_a_retirement_and_an_uncaused_release_read_detached(
    tmp_path: Path, cause: RouteReleaseCause | None
) -> None:
    store, engine = _store(tmp_path)
    _route_and_release(store, engine, runner_id="rn_first", at=1, cause=cause or RouteReleaseCause.DETACH)
    if cause is None:
        # A row written before the cause was recorded is reachable only by writing the column directly.
        with hub_store_connections(engine).write("seed") as conn:
            conn.execute(hub_scoped(sa.update(s.route_released).values(cause=None)))
    row = _row_for(store, "detached")
    assert row.runner_id is None
    assert row.reason is None


@pytest.mark.parametrize("cause", sorted(set(RouteReleaseCause) - OWN_OCCURRENCE_RELEASE_CAUSES))
def test_a_release_written_as_part_of_another_fact_gets_no_row_and_takes_no_page_slot(
    tmp_path: Path, cause: RouteReleaseCause
) -> None:
    store, engine = _store(tmp_path)
    _route_and_release(store, engine, runner_id="rn_first", at=1, cause=RouteReleaseCause.DETACH)
    # Newer than the detach and more than the page holds.
    for at in (3, 5, 7):
        _route_and_release(store, engine, runner_id="rn_later", at=at, cause=cause)

    rows = store.events.activity_facts_since(_T0, limit=2)

    assert [(r.cause, r.at) for r in rows if r.key.startswith("route_released:")] == [("detached", _at(2))]


def test_a_finalized_delivery_has_no_row(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_claim(store, engine)
    with chunk_unit(hub_store_connections(engine), "ch_1") as tx:
        store.delivery.finalize_delivery(
            "ch_1",
            transition_id="tr_landed",
            from_node_id="nd_deliver",
            to_node_id=RESERVED_TERMINAL,
            choice_name="success",
            epoch=2,
            runner_id="hub",
            at=_at(2),
            close_decision=decide_close_enqueue([], lambda _: True),
            transaction=tx,
        )

    with hub_store_connections(engine).write("read") as conn:
        causes = conn.execute(hub_scoped(sa.select(s.route_released.c.cause))).scalars().all()

    assert causes == [RouteReleaseCause.DELIVERY.value]
    rows = store.events.activity_facts_since(_T0, limit=10)
    assert [r.key for r in rows if r.key.startswith("route_released:")] == []


def test_paused_reads_off_chunk_pause_facts(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    store.lifecycle.record_pause("ch_1", paused=True, by="alice", at=_at(1))
    row = _row_for(store, "paused")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("chunk_pause_facts:")


def test_resumed_reads_off_chunk_pause_facts(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    store.lifecycle.record_pause("ch_1", paused=True, by="alice", at=_at(1))
    store.lifecycle.record_pause("ch_1", paused=False, by="alice", at=_at(2))
    row = _row_for(store, "resumed")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("chunk_pause_facts:")


def test_stopped_reads_off_chunk_stopped(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_stop(store, engine)
    row = _row_for(store, "stopped")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("chunk_stopped:")


def test_completed_reads_off_chunk_completed(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_completion(store, engine)
    row = _row_for(store, "completed")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("chunk_completed:")


def test_restarted_reads_off_chunk_restarts(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_restart(store, engine)
    row = _row_for(store, "restarted")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("chunk_restarts:")


def test_deleted_reads_off_chunk_deleted(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    with hub_store_connections(engine).write("seed") as conn:
        record_deleted_row(conn, "ch_1", by="alice", at=_at(1))
    row = _row_for(store, "deleted")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("chunk_deleted:")
    assert row.by == "alice"


def test_deleted_chunk_shows_only_its_own_deletion_row(tmp_path: Path) -> None:
    """``activity_facts_since`` excludes a deleted chunk's history from every OTHER
    block — only the ``chunk_deleted`` row itself survives the filter."""
    store, engine = _store(tmp_path)
    seed_promote(store, engine, "ch_1", at=_at(1))
    with hub_store_connections(engine).write("seed") as conn:
        record_deleted_row(conn, "ch_1", by="alice", at=_at(2))

    causes = {r.cause for r in store.events.activity_facts_since(_T0, limit=50) if r.chunk_id == "ch_1"}

    assert causes == {"deleted"}


#: How to give a chunk one fact in each source that filters deleted chunks out, keyed by the source's table.
_DELETABLE_FACTS: dict[str, Callable[[ChunkStores, sa.Engine], object]] = {
    "chunk_promoted": lambda store, engine: seed_promote(store, engine, "ch_1", at=_at(1)),
    "chunk_demoted": _seed_demotion,
    "route_created": _seed_claim,
    "transitions": partial(_seed_transition, runner_id="runner-a", choice_name="approve"),
    "chunk_migrations": _seed_migration,
    "chunk_restarts": _seed_restart,
    "decisions": _seed_decision,
    "decision_resolutions": _seed_decision_resolution,
    "questions": _seed_question,
    "question_answers": _seed_question_answer,
    "escalations": _seed_escalation,
    "requeues": _seed_requeue,
    "route_released": _seed_release,
    "chunk_pause_facts": lambda store, _: store.lifecycle.record_pause("ch_1", paused=True, by="alice", at=_at(1)),
    "chunk_stopped": _seed_stop,
    "chunk_completed": _seed_completion,
}


def test_every_source_that_filters_deleted_chunks_has_a_deletion_case() -> None:
    """Grouping needs no deletion case, since a grouped chunk cannot be deleted, and the deletion row is the one a
    deletion keeps."""
    unfiltered = {"chunk_grouped", "chunk_deleted"}
    assert set(_DELETABLE_FACTS) == {table.name for table in EVENT_SOURCES} - unfiltered


@pytest.mark.parametrize("source", sorted(_DELETABLE_FACTS))
def test_a_deleted_chunks_fact_is_suppressed_in_every_source(tmp_path: Path, source: str) -> None:
    store, engine = _store(tmp_path)
    _DELETABLE_FACTS[source](store, engine)
    before = store.events.activity_facts_since(_T0, limit=50)
    assert any(r.key.startswith(f"{source}:") for r in before), f"no {source} row to suppress: {before}"
    with hub_store_connections(engine).write("seed") as conn:
        record_deleted_row(conn, "ch_1", by="alice", at=_at(3))

    rows = store.events.activity_facts_since(_T0, limit=50)

    assert [r.cause for r in rows if r.chunk_id == "ch_1"] == ["deleted"]


def test_grouped_chunks_history_is_unaffected_by_the_deleted_exclusion(tmp_path: Path) -> None:
    """Grouping's own feed behavior is unchanged — only a *deleted* chunk's history
    drops away, a *grouped* one's still shows."""
    store, engine = _store(tmp_path)
    _seed_second_chunk(engine, "ch_2")
    seed_promote(store, engine, "ch_1", at=_at(1))
    with hub_store_connections(engine).write("seed") as conn:
        record_grouped_row_conn(conn, "ch_1", grouped_into="ch_2", at=_at(2))

    causes = {r.cause for r in store.events.activity_facts_since(_T0, limit=50) if r.chunk_id == "ch_1"}

    assert causes == {"minted", "promoted", "grouped"}


def test_edited_produces_no_activity_row(tmp_path: Path) -> None:
    """No fact table backs ``edited`` — a deliberate exclusion, not a gap."""
    store, engine = _store(tmp_path)
    with chunk_unit(hub_store_connections(engine), "ch_1") as tx:
        store.record.set_graph("ch_1", graph_id="gr_2", transaction=tx)
        store.record.set_defaults(
            "ch_1",
            default_model=["opus"],
            default_effort="high",
            default_harnesses=[],
            transaction=tx,
        )
    rows = store.events.activity_facts_since(_T0, limit=50)
    assert all(r.cause != "edited" for r in rows)


def test_the_activity_cause_vocabulary_is_every_chunk_change_cause_but_edited() -> None:
    """The board reads the generated ``ActivityChunkChangeCause`` to tell a live frame that is
    an activity occurrence from one that is not."""
    assert set(get_args(ChunkChangeCause)) - set(get_args(ActivityChunkChangeCause)) == {"edited"}
    assert set(get_args(ActivityChunkChangeCause)) <= set(get_args(ChunkChangeCause))


# --- the since window and the per-source limit bound -------------------------


def test_since_window_excludes_an_older_fact(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    seed_promote(store, engine, "ch_1", at=_at(1))
    excluded = [r for r in store.events.activity_facts_since(_at(2), limit=50) if r.cause == "promoted"]
    assert excluded == []
    included = [r for r in store.events.activity_facts_since(_at(1), limit=50) if r.cause == "promoted"]
    assert len(included) == 1


def test_no_single_source_read_exceeds_the_limit(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    for i in range(10):
        store.lifecycle.record_pause("ch_1", paused=(i % 2 == 0), by="alice", at=_at(i))
    rows = store.events.activity_facts_since(_T0, limit=3)
    pause_rows = [r for r in rows if r.cause in ("paused", "resumed")]
    assert len(pause_rows) == 3  # chunk_pause_facts' own bounded read, capped at `limit`


# --- the runner-pause seam: IReadRunnerRegistry, never IReadChunkEventsRepository --


def _registry_store(tmp_path: Path) -> RunnerRegistryStore:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migrated_hub_at(db_url)
    store = RunnerRegistryStore(hub_store_connections(create_engine_from_url(db_url)))
    store.add(RunnerAddition("runner-a", "runner-a", "hash-runner-a", at=_T0, by="test"))
    return store


def test_runner_pause_resolves_through_the_runner_registry(tmp_path: Path) -> None:
    store = _registry_store(tmp_path)
    store.record_registration("runner-a", workspace_id="ws-a", env_capacity=None, at=_T0)
    store.record_pause("runner-a", paused=True, at=_at(1), by="alice")
    store.record_local_pause("runner-a", paused=True, at=_at(2), by="operator", reason="spend cap hit")

    rows = store.list_pause_facts_since(_T0, limit=50)

    fleet = [r for r in rows if not r.local]
    local = [r for r in rows if r.local]
    assert len(fleet) == 1
    assert fleet[0].paused
    assert fleet[0].runner_id == "runner-a"
    assert fleet[0].by == "alice"
    assert fleet[0].key.startswith("runner_pause_facts:")
    assert len(local) == 1
    assert local[0].paused
    assert local[0].runner_id == "runner-a"
    assert local[0].by == "operator"
    assert local[0].reason == "spend cap hit"
    assert local[0].key.startswith("runner_local_pause_facts:")


def test_registered_and_heartbeat_kinds_are_never_sourced(tmp_path: Path) -> None:
    """Pause family only — ``registered``/``heartbeat`` carry no fact table."""
    store = _registry_store(tmp_path)
    store.record_registration("runner-a", workspace_id="ws-a", env_capacity=None, at=_T0)
    store.touch_last_seen("runner-a", at=_at(1))
    rows = store.list_pause_facts_since(_T0, limit=50)
    assert rows == []


def test_read_chunk_repository_gains_no_runner_pause_method() -> None:
    """The seam boundary is honored: runner-pause reads live on ``IReadRunnerRegistry`` alone."""
    assert hasattr(IReadRunnerRegistry, "list_pause_facts_since")
    assert not hasattr(IReadChunkEventsRepository, "list_pause_facts_since")
    assert hasattr(IReadChunkEventsRepository, "activity_facts_since")
