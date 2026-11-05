"""Helpers for the load all facts store tests, shared with other test modules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import Engine, insert

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.migration_source import MigrationSource
from blizzard.hub.domain.chunk.model import (
    Chunk,
    DecisionChoice,
    decide_close_enqueue,
)
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from blizzard.hub.domain.runners.route import Route
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_rows import record_deleted_row, record_grouped_row_conn
from tests.support.scaffolding import (
    chunk_stores,
    chunk_unit,
    hub_store_connections,
    migrate_to,
    seed_chunk_record,
    seed_graph,
    seed_lease,
    stamp,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _seed_node(conn: sa.Connection, graph_id: str, node_id: str, *, executor: str) -> None:
    conn.execute(
        insert(s.graph_nodes).values(
            stamp(
                s.graph_nodes,
                node_id=node_id,
                graph_id=graph_id,
                name=node_id,
                executor=executor,
                session="resume",
                judged_by="worker",
            )
        )
    )


def seeded_chunk_stores(tmp_path: Path) -> tuple[ChunkStores, Engine, FixedClock]:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=T0)
        seed_graph(conn, "gr_2", at=T0)
        _seed_node(conn, "gr_1", "nd_g1_runner", executor="runner")
        _seed_node(conn, "gr_1", "nd_g1_hub", executor="hub")
        _seed_node(conn, "gr_2", "nd_g2_runner", executor="runner")
        _seed_node(conn, "gr_2", "nd_g2_hub", executor="hub")
    clock = FixedClock(T0)
    return chunk_stores(engine, clock), engine, clock


def _mint(store: ChunkStores, chunk_id: str, *, graph_id: str = "gr_1") -> None:
    seed_chunk_record(store, Chunk(chunk_id=chunk_id, graph_id=graph_id, work_refs=[], minted_at=T0))


def seed_fixture(store: ChunkStores, engine: Engine, clock: FixedClock) -> None:
    """One chunk per derived status, plus the AC4 edge shapes and a kitchen-sink chunk
    touching every other fact family ``load_facts`` reads."""
    _mint(store, "ch_not_ready")  # a chunks-only row (AC4): derives NOT_READY

    _mint(store, "ch_ready")
    store.queue.record_promote("ch_ready", at=T0)

    _mint(store, "ch_running")
    store.queue.record_promote("ch_running", at=T0)
    with store.exclusive.locked(["ch_running"]) as handle:
        store.route.record_route_locked(
            handle,
            Route(chunk_id="ch_running", runner_id="r1", workspace_id="w1", environment_ids=["e1"], created_at=T0),
            token_hash="th_running",
            at=T0,
        )

    _mint(store, "ch_delivering")
    store.queue.record_promote("ch_delivering", at=T0)
    store.movement.record_transition(
        transition_id="tr_delivering",
        chunk_id="ch_delivering",
        from_node_id=None,
        to_node_id="nd_g1_hub",
        choice_name=None,
        epoch=1,
        runner_id="r",
        at=T0,
        artifacts=[],
        admission=EpochAdmission.AT_OR_ABOVE,
        close_decision=decide_close_enqueue([], lambda _: True),
    )

    _mint(store, "ch_waiting")
    store.queue.record_promote("ch_waiting", at=T0)
    store.questions.record_question(
        question_id="qn_1",
        chunk_id="ch_waiting",
        node_id=None,
        session_id=None,
        runner_id="r",
        epoch=1,
        question="which branch?",
        options=[],
        asked_at=T0,
        admission=EpochAdmission.AT_OR_ABOVE,
    )

    _mint(store, "ch_needs")
    store.queue.record_promote("ch_needs", at=T0)
    store.escalations.record_escalation(
        "ch_needs",
        epoch=1,
        takeover_command="cmd",
        at=T0,
        admission=EpochAdmission.AT_OR_ABOVE,
        cause=None,
        detail=None,
    )

    _mint(store, "ch_paused")
    store.queue.record_promote("ch_paused", at=T0)
    # Newest-fact-wins: paused, then resumed, then paused again — the tail
    # (paused=True) must win, proving the explicit pause order_by round-trips.
    store.lifecycle.record_pause("ch_paused", paused=True, by="op", at=_at(0))
    store.lifecycle.record_pause("ch_paused", paused=False, by="op", at=_at(1))
    store.lifecycle.record_pause("ch_paused", paused=True, by="op", at=_at(2))

    _mint(store, "ch_stopped")
    store.queue.record_promote("ch_stopped", at=T0)
    with store.exclusive.locked(["ch_stopped"]) as handle:
        store.route.record_route_locked(
            handle,
            Route(chunk_id="ch_stopped", runner_id="r1", workspace_id="w1", environment_ids=["e1"], created_at=T0),
            token_hash="th_stopped",
            at=T0,
        )
    clock.instant = _at(1)
    with store.exclusive.locked(["ch_stopped"]) as handle:
        store.lifecycle.record_stop_locked(handle, "ch_stopped", by="op", at=_at(1))

    _mint(store, "ch_done_completed")
    store.queue.record_promote("ch_done_completed", at=T0)
    clock.instant = T0
    with store.exclusive.locked(["ch_done_completed"]) as handle:
        store.lifecycle.record_completion_locked(
            handle, "ch_done_completed", by="op", at=T0, close_decision=decide_close_enqueue([], lambda _: True)
        )

    _mint(store, "ch_done_terminal")
    store.queue.record_promote("ch_done_terminal", at=T0)
    store.movement.record_transition(
        transition_id="tr_terminal",
        chunk_id="ch_done_terminal",
        from_node_id="nd_g1_runner",
        to_node_id=RESERVED_TERMINAL,
        choice_name="go",
        epoch=1,
        runner_id="r",
        at=T0,
        artifacts=[],
        admission=EpochAdmission.AT_OR_ABOVE,
        close_decision=decide_close_enqueue([], lambda _: True),
    )

    # AC4: movement facts spanning two graphs — a transition on gr_1, then a migration
    # landing on gr_2's hub node. The executor for that landing must resolve against
    # gr_2, not the chunk's gr_1 mint pin.
    _mint(store, "ch_multigraph", graph_id="gr_1")
    store.queue.record_promote("ch_multigraph", at=T0)
    store.movement.record_transition(
        transition_id="tr_multigraph",
        chunk_id="ch_multigraph",
        from_node_id=None,
        to_node_id="nd_g1_runner",
        choice_name=None,
        epoch=1,
        runner_id="r",
        at=T0,
        artifacts=[],
        admission=EpochAdmission.AT_OR_ABOVE,
        close_decision=decide_close_enqueue([], lambda _: True),
    )
    store.movement.record_migration(
        "ch_multigraph",
        from_node_id="nd_g1_runner",
        from_graph_id="gr_1",
        to_graph_id="gr_2",
        landed_node_id="nd_g2_hub",
        choice_name="go",
        model=None,
        epoch=2,
        at=_at(1),
        artifacts=[],
        source=MigrationSource.AUTHORED_EDGE,
        admission=EpochAdmission.AT_OR_ABOVE,
        close_decision=decide_close_enqueue([], lambda _: True),
    )

    # AC4: grouped-away and deleted chunks are ephemeral — excluded from every read.
    _mint(store, "ch_grouped")
    store.queue.record_promote("ch_grouped", at=T0)
    with hub_store_connections(engine).write("seed") as conn:
        record_grouped_row_conn(conn, "ch_grouped", grouped_into="ch_ready", at=T0)

    _mint(store, "ch_deleted")
    with hub_store_connections(engine).write("seed") as conn:
        record_deleted_row(conn, "ch_deleted", by="op", at=T0)

    # A kitchen-sink chunk exercising every remaining fact family ``load_facts`` reads,
    # so the bulk read's completeness is proven, not merely asserted.
    _mint(store, "ch_kitchen_sink")
    store.queue.record_promote("ch_kitchen_sink", at=T0)
    seed_lease(engine, "ch_kitchen_sink", epoch=1, runner_id="r", at=T0)
    with store.exclusive.locked(["ch_kitchen_sink"]) as handle:
        store.route.record_route_locked(
            handle,
            Route(chunk_id="ch_kitchen_sink", runner_id="r1", workspace_id="w1", environment_ids=["e1"], created_at=T0),
            token_hash="th_ks_1",
            at=T0,
        )
    with store.exclusive.locked(["ch_kitchen_sink"]) as handle:
        store.route.record_route_token_locked(handle, "ch_kitchen_sink", token_hash="th_ks_2", at=_at(1))
    with store.exclusive.locked(["ch_kitchen_sink"]) as handle:
        store.route.record_route_released_locked(handle, "ch_kitchen_sink", at=_at(2))
        store.movement.record_requeue_locked(handle, "ch_kitchen_sink", at=_at(2))
    store.usage.record_usage(
        "ch_kitchen_sink",
        node_id="nd_g1_runner",
        epoch=1,
        runner_id="r",
        kind="agent_turn",
        model="m",
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=0.1,
        at=T0,
    )
    with store.exclusive.locked(["ch_kitchen_sink"]) as handle:
        store.escalations.record_bounce_locked(
            handle,
            "ch_kitchen_sink",
            epoch=1,
            admission=EpochAdmission.AT_OR_ABOVE,
            cause="conflict",
            envelope="{}",
            at=T0,
        )
        store.hub_exec.record_hub_node_poll_locked(
            handle, "ch_kitchen_sink", node_id="nd_g1_hub", epoch=1, kind="pending", at=T0
        )
    store.decisions.record_decision(
        imposed_by_runner_id=None,
        decision_id="dec_1",
        chunk_id="ch_kitchen_sink",
        node_id="nd_g1_hub",
        node_name="n",
        epoch=2,
        choices=[DecisionChoice(name="ok", description="d")],
        at=T0,
        artifacts=[],
        admission=EpochAdmission.AT_OR_ABOVE,
        close_decision=decide_close_enqueue([], lambda _: True),
    )
    with chunk_unit(hub_store_connections(engine), "ch_kitchen_sink") as unit:
        store.decisions.record_decision_resolution("dec_1", choice="ok", resolved_by="op", at=_at(1), transaction=unit)
    store.delivery.record_delivery_repo_landed(
        "ch_kitchen_sink",
        repo="r1",
        commit_hash="c1",
        repository_id=None,
        at=T0,
        close_decision=decide_close_enqueue([], lambda _: True),
    )
    store.delivery.record_delivery_landed(
        "ch_kitchen_sink", at=T0, close_decision=decide_close_enqueue([], lambda _: True)
    )
    with store.exclusive.locked(["ch_kitchen_sink"]) as handle:
        store.movement.record_restart_locked(
            handle, "ch_kitchen_sink", from_node_id="nd_g1_runner", to_node_id="nd_g1_runner", by="op", at=_at(3)
        )


LIVE_CHUNK_IDS = [
    "ch_not_ready",
    "ch_ready",
    "ch_running",
    "ch_delivering",
    "ch_waiting",
    "ch_needs",
    "ch_paused",
    "ch_stopped",
    "ch_done_completed",
    "ch_done_terminal",
    "ch_multigraph",
    "ch_kitchen_sink",
]
