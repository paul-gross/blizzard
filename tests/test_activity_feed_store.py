"""``IReadChunkRepository.activity_facts_since`` / ``IReadRunnerRegistry.list_pause_facts_since``
(component tier) — the activity feed's bounded per-source reads (issue #213, AC4).

One case per ``ChunkChangeCause`` member mapped to a fact table, plus the ``since``
window and the per-source ``limit`` bound, against a real store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.registry import IReadRunnerRegistry
from blizzard.hub.domain.work import ActivityRow, DecisionChoice, IReadChunkRepository, MigrationSource
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.internal.chunk_store import ChunkStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from tests.support import migrate_to, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _store(tmp_path: Path) -> tuple[ChunkStore, sa.Engine]:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        seed_graph(conn, "gr_2", at=_T0)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_T0)
    return ChunkStore(engine, FixedClock(_T0)), engine


def _seed_second_chunk(engine: sa.Engine, chunk_id: str) -> None:
    """Seed a second chunk (e.g. a group survivor) directly — no store method mints a
    bare ``chunks`` row with no work refs."""
    with engine.begin() as conn:
        seed_chunk(conn, chunk_id, graph_id="gr_1", at=_T0)


def _row_for(store: ChunkStore, cause: str, *, since: datetime = _T0, limit: int = 50) -> ActivityRow:
    rows = [r for r in store.activity_facts_since(since, limit=limit) if r.cause == cause]
    assert len(rows) == 1, f"expected exactly one {cause!r} row, got {rows}"
    return rows[0]


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
    store, _ = _store(tmp_path)
    store.record_promote("ch_1", at=_at(1))
    row = _row_for(store, "promoted")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("chunk_promoted:")
    assert row.at == _at(1)


def test_grouped_reads_off_chunk_grouped(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_second_chunk(engine, "ch_2")
    store.record_grouped("ch_1", grouped_into="ch_2", at=_at(1))
    row = _row_for(store, "grouped")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("chunk_grouped:")


def test_claimed_reads_off_route_created(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    store.record_route(
        Route(chunk_id="ch_1", runner_id="runner-a", workspace_id="ws-a", environment_ids=[], created_at=_at(1)),
        token_hash="deadbeef",
        at=_at(1),
    )
    row = _row_for(store, "claimed")
    assert row.chunk_id == "ch_1"
    assert row.runner_id == "runner-a"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("route_created:")


def test_node_completed_reads_off_transitions_authored_by_a_runner(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    store.record_transition(
        transition_id="tr_1",
        chunk_id="ch_1",
        from_node_id=None,
        to_node_id="nd_b",
        choice_name="approve",
        epoch=1,
        runner_id="runner-a",
        at=_at(1),
        artifacts=[],
    )
    row = _row_for(store, "node-completed")
    assert row.chunk_id == "ch_1"
    assert row.runner_id == "runner-a"
    assert row.graph_id == "gr_1"
    assert row.key == "transitions:tr_1"


def test_hub_advanced_reads_off_transitions_authored_by_the_hub_coordinator(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    store.record_transition(
        transition_id="tr_1",
        chunk_id="ch_1",
        from_node_id=None,
        to_node_id="nd_b",
        choice_name=None,
        epoch=1,
        runner_id="hub",
        at=_at(1),
        artifacts=[],
    )
    row = _row_for(store, "hub-advanced")
    assert row.chunk_id == "ch_1"
    assert row.runner_id == "hub"
    assert row.graph_id == "gr_1"
    assert row.key == "transitions:tr_1"


def test_migrated_reads_off_chunk_migrations(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    store.record_migration(
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
    )
    row = _row_for(store, "migrated")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_2"  # the migration's target graph, not the one migrated out of
    assert row.key.startswith("chunk_migrations:")


def test_decision_submitted_reads_off_decisions(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    store.record_decision(
        decision_id="dec_1",
        chunk_id="ch_1",
        node_id="nd_gate",
        node_name="gate",
        epoch=1,
        choices=[DecisionChoice(name="go", description="go")],
        at=_at(1),
        artifacts=[],
    )
    row = _row_for(store, "decision-submitted")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key == "decisions:dec_1"


def test_decision_resolved_reads_off_decision_resolutions(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    store.record_decision(
        decision_id="dec_1",
        chunk_id="ch_1",
        node_id="nd_gate",
        node_name="gate",
        epoch=1,
        choices=[DecisionChoice(name="go", description="go")],
        at=_at(1),
        artifacts=[],
    )
    store.record_decision_resolution("dec_1", choice="go", resolved_by="alice", at=_at(2))
    row = _row_for(store, "decision-resolved")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key == "decision_resolutions:dec_1"


def test_question_asked_reads_off_questions(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    store.record_question(
        question_id="qn_1",
        chunk_id="ch_1",
        node_id="nd_a",
        session_id=None,
        runner_id="runner-a",
        epoch=1,
        question="continue?",
        options=[],
        asked_at=_at(1),
    )
    row = _row_for(store, "question-asked")
    assert row.chunk_id == "ch_1"
    assert row.runner_id == "runner-a"
    assert row.graph_id == "gr_1"
    assert row.key == "questions:qn_1"


def test_question_answered_reads_off_question_answers(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    store.record_question(
        question_id="qn_1",
        chunk_id="ch_1",
        node_id="nd_a",
        session_id=None,
        runner_id="runner-a",
        epoch=1,
        question="continue?",
        options=[],
        asked_at=_at(1),
    )
    store.answer_question("qn_1", answer="yes", answered_by="alice", at=_at(2))
    row = _row_for(store, "question-answered")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key == "question_answers:qn_1"


def test_escalated_reads_off_escalations(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    store.record_escalation("ch_1", epoch=1, takeover_command="cd x && resume", at=_at(1))
    row = _row_for(store, "escalated")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("escalations:")


def test_requeued_reads_off_requeues(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    store.record_requeue("ch_1", at=_at(1))
    row = _row_for(store, "requeued")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("requeues:")


def test_detached_reads_off_route_released(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    store.record_route_released("ch_1", at=_at(1))
    row = _row_for(store, "detached")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("route_released:")


def test_paused_reads_off_chunk_pause_facts(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    store.record_pause("ch_1", paused=True, by="alice", at=_at(1))
    row = _row_for(store, "paused")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("chunk_pause_facts:")


def test_resumed_reads_off_chunk_pause_facts(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    store.record_pause("ch_1", paused=True, by="alice", at=_at(1))
    store.record_pause("ch_1", paused=False, by="alice", at=_at(2))
    row = _row_for(store, "resumed")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("chunk_pause_facts:")


def test_stopped_reads_off_chunk_stopped(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    store.record_stop("ch_1", by="alice", at=_at(1))
    row = _row_for(store, "stopped")
    assert row.chunk_id == "ch_1"
    assert row.graph_id == "gr_1"
    assert row.key.startswith("chunk_stopped:")


def test_edited_produces_no_activity_row(tmp_path: Path) -> None:
    """No fact table backs ``edited`` — a deliberate exclusion, not a gap."""
    store, _ = _store(tmp_path)
    store.set_graph("ch_1", graph_id="gr_2")
    store.set_defaults("ch_1", default_model=["opus"], default_effort="high")
    rows = store.activity_facts_since(_T0, limit=50)
    assert all(r.cause != "edited" for r in rows)


# --- the since window and the per-source limit bound -------------------------


def test_since_window_excludes_an_older_fact(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    store.record_promote("ch_1", at=_at(1))
    excluded = [r for r in store.activity_facts_since(_at(2), limit=50) if r.cause == "promoted"]
    assert excluded == []
    included = [r for r in store.activity_facts_since(_at(1), limit=50) if r.cause == "promoted"]
    assert len(included) == 1


def test_no_single_source_read_exceeds_the_limit(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    for i in range(10):
        store.record_pause("ch_1", paused=(i % 2 == 0), by="alice", at=_at(i))
    rows = store.activity_facts_since(_T0, limit=3)
    pause_rows = [r for r in rows if r.cause in ("paused", "resumed")]
    assert len(pause_rows) == 3  # chunk_pause_facts' own bounded read, capped at `limit`


# --- the runner-pause seam: IReadRunnerRegistry, never IReadChunkRepository --


def _registry_store(tmp_path: Path) -> RunnerRegistryStore:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    return RunnerRegistryStore(create_engine_from_url(db_url))


def test_runner_pause_resolves_through_the_runner_registry(tmp_path: Path) -> None:
    store = _registry_store(tmp_path)
    store.upsert_registration("runner-a", workspace_id="ws-a", env_capacity=None, at=_T0)
    store.record_pause("runner-a", paused=True, at=_at(1), by="alice")
    store.record_local_pause("runner-a", paused=True, at=_at(2), by="operator", reason="spend cap hit")

    rows = store.list_pause_facts_since(_T0, limit=50)

    fleet = [r for r in rows if r.kind == "paused"]
    local = [r for r in rows if r.kind == "locally-paused"]
    assert len(fleet) == 1
    assert fleet[0].type == "runner-changed"
    assert fleet[0].runner_id == "runner-a"
    assert fleet[0].by == "alice"
    assert fleet[0].key.startswith("runner_pause_facts:")
    assert len(local) == 1
    assert local[0].runner_id == "runner-a"
    assert local[0].by == "operator"
    assert local[0].reason == "spend cap hit"
    assert local[0].key.startswith("runner_local_pause_facts:")


def test_registered_and_heartbeat_kinds_are_never_sourced(tmp_path: Path) -> None:
    """Pause family only (issue #213) — ``registered``/``heartbeat`` carry no fact table."""
    store = _registry_store(tmp_path)
    store.upsert_registration("runner-a", workspace_id="ws-a", env_capacity=None, at=_T0)
    store.touch_last_seen("runner-a", at=_at(1))
    rows = store.list_pause_facts_since(_T0, limit=50)
    assert rows == []


def test_read_chunk_repository_gains_no_runner_pause_method() -> None:
    """The seam boundary is honored: runner-pause reads live on ``IReadRunnerRegistry`` alone."""
    assert hasattr(IReadRunnerRegistry, "list_pause_facts_since")
    assert not hasattr(IReadChunkRepository, "list_pause_facts_since")
    assert hasattr(IReadChunkRepository, "activity_facts_since")
