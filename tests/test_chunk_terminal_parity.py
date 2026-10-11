"""Every SQL form of "the chunk is terminal" agrees with :meth:`ChunkFacts.status` (component tier).

The open-question list, the open-decision list, the write fence and the live prefilter each ask the one
``chunk_is_terminal`` builder, so over a world seeded across every terminal branch they all read the
chunk the way the derivation does. The prefilter may only keep extra residue — never drop a chunk the
derivation calls live."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import (
    Column,
    ColumnElement,
    Connection,
    DateTime,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
)
from structlog import get_logger

from blizzard.foundation.chunk_status import TERMINAL_STATUSES, ChunkStatus
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreErrorFactory
from blizzard.hub.store.internal import chunk_rows
from blizzard.hub.store.internal.chunk_rows import (
    QUESTIONS,
    ephemeral_ids_select,
    fence,
    lock_chunk_row,
)
from blizzard.hub.store.internal.chunk_terminal_predicates import maybe_live, settled_done, settled_stopped
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import StoreScope, TenantId
from tests.support import HubHarness, hub_store_connections, stamp
from tests.support_chunk_world import every_branch, hub_with_graph

pytestmark = pytest.mark.component

_EPOCH = 99


def _park_a_question_and_a_decision_on_every_chunk(hub: HubHarness, chunk_ids: list[str]) -> None:
    at = hub.clock.now()
    with hub.engine.begin() as conn:
        for chunk_id in chunk_ids:
            conn.execute(
                insert(s.questions).values(
                    stamp(
                        s.questions,
                        question_id=f"qn_{chunk_id}",
                        chunk_id=chunk_id,
                        node_id="nd_1",
                        runner_id="r",
                        epoch=_EPOCH,
                        question="continue?",
                        options="[]",
                        asked_at=at,
                    )
                )
            )
            conn.execute(
                insert(s.decisions).values(
                    stamp(
                        s.decisions,
                        decision_id=f"dec_{chunk_id}",
                        chunk_id=chunk_id,
                        node_id="nd_1",
                        node_name="gate",
                        epoch=_EPOCH,
                        choices=json.dumps([{"name": "ok", "description": "go"}]),
                        submitted_at=at,
                    )
                )
            )


def _fence_refuses_as_terminal(hub: HubHarness, chunk_id: str) -> bool:
    with hub_store_connections(hub.engine).write("seed") as conn:
        lock_chunk_row(conn, chunk_id)
        refusal = fence(conn, chunk_id, epoch=_EPOCH, admission=EpochAdmission.AT_OR_ABOVE)
    return refusal is not None and refusal.latest is None


def test_every_terminal_form_agrees_with_the_derived_status(tmp_path: Path) -> None:
    hub = hub_with_graph(tmp_path)
    every_branch(hub)
    facts = hub.services.chunks.facts.load_all_facts()
    # A terminal and a non-terminal transition at an identical instant and epoch derive by fact order
    # and no write path records it, so the predicate leaves it live — the one residue.
    residue = {chunk_id for chunk_id in facts if chunk_id.startswith("ch_mixed_tie_")}
    decided = {chunk_id: f.status() for chunk_id, f in facts.items() if chunk_id not in residue}
    live_ids = {chunk_id for chunk_id, status in decided.items() if status not in TERMINAL_STATUSES}
    terminal_ids = set(decided) - live_ids
    assert "ch_migrated_old" in live_ids  # a migration wins its tie with a terminal transition by kind rank
    assert {"ch_tie_old", "ch_done_transition_old", "ch_stopped_old"} <= terminal_ids
    _park_a_question_and_a_decision_on_every_chunk(hub, sorted(facts))

    open_question_chunks = {q.chunk_id for q in hub.services.chunks.questions.list_open_questions()} - residue
    open_decision_chunks = {d.chunk_id for d in hub.services.chunks.decisions.list_open_decisions()} - residue
    connections = hub_store_connections(hub.engine)
    with connections.read("maybe_live") as conn:
        kept = set(
            conn.execute(connections.select(s.chunks, s.chunks.c.chunk_id).where(maybe_live(connections))).scalars()
        )

    assert open_question_chunks == live_ids
    assert open_decision_chunks == live_ids
    assert live_ids | residue <= kept
    for chunk_id in sorted(decided):
        assert _fence_refuses_as_terminal(hub, chunk_id) is (chunk_id in terminal_ids), chunk_id


def test_newest_done_ranks_by_the_derived_completion_instant(tmp_path: Path) -> None:
    hub = hub_with_graph(tmp_path)
    every_branch(hub)
    visible = {c.chunk_id for c in hub.services.chunks.record.list_all()}
    ranked = sorted(
        (
            (f.completed_at(), chunk_id)
            for chunk_id, f in hub.services.chunks.facts.load_all_facts().items()
            if chunk_id in visible
            and not chunk_id.startswith("ch_mixed_tie_")  # the tie residue the SQL form leaves live
            and f.status() is ChunkStatus.DONE
            and f.completed_at() is not None
        ),
        reverse=True,
    )
    expected = [chunk_id for _, chunk_id in ranked]
    assert len(expected) > 2
    for limit in (1, 2, len(expected), len(expected) + 5):
        assert hub.services.chunks.record.newest_done(limit=limit) == expected[:limit], limit


# --- every nested statement is confined to the scope's tenant ------------------------------------
#
# The live schema carries no tenant key yet, so these run the shared builders over stand-in tables
# that do. Two tenants hold the same chunk and question ids with opposite facts: a predicate, a
# ``UNION`` arm or an outer join that read the other tenant's rows would answer for the wrong chunk.

TEN_A = TenantId("ten_A")
TEN_B = TenantId("ten_B")
_T1 = datetime(2026, 1, 1, tzinfo=UTC)
_T2 = datetime(2026, 1, 2, tzinfo=UTC)

_md = MetaData()


def _tenant_table(name: str, *columns: Column[Any]) -> Table:
    return Table(
        name,
        _md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("tenant_id", String, nullable=False),
        Column("chunk_id", String, nullable=False),
        *columns,
    )


def _movement(name: str) -> Table:
    return _tenant_table(
        name, Column("recorded_at", DateTime, nullable=False), Column("epoch", Integer, nullable=False)
    )


_STAND_INS = {
    "chunks": _tenant_table("chunks"),
    "chunk_stopped": _tenant_table("chunk_stopped", Column("stopped_at", DateTime, nullable=False)),
    "chunk_completed": _tenant_table("chunk_completed", Column("completed_at", DateTime, nullable=False)),
    "transitions": _tenant_table(
        "transitions",
        Column("transition_id", String, nullable=False),
        Column("to_node_id", String, nullable=False),
        Column("recorded_at", DateTime, nullable=False),
        Column("epoch", Integer, nullable=False),
    ),
    "chunk_migrations": _movement("chunk_migrations"),
    "chunk_restarts": _movement("chunk_restarts"),
    "chunk_grouped": _tenant_table("chunk_grouped"),
    "chunk_deleted": _tenant_table("chunk_deleted"),
    "questions": _tenant_table("questions", Column("question_id", String, nullable=False)),
    "question_answers": _tenant_table(
        "question_answers",
        Column("question_id", String, nullable=False),
        Column("answer", String, nullable=False),
        Column("answered_by", String, nullable=False),
        Column("answered_at", DateTime, nullable=False),
    ),
    "answer_deliveries": _tenant_table(
        "answer_deliveries",
        Column("question_id", String, nullable=False),
        Column("delivered_at", DateTime, nullable=False),
    ),
}


def _seed(conn: Connection) -> None:
    t = _STAND_INS

    def add(table: str, tenant: TenantId, chunk_id: str, **values: Any) -> None:
        conn.execute(insert(t[table]).values(tenant_id=tenant, chunk_id=chunk_id, **values))

    for tenant in (TEN_A, TEN_B):
        for chunk_id in ("ch_live", "ch_stopped", "ch_done", "ch_fresh"):
            add("chunks", tenant, chunk_id)
    # Tenant A: ch_stopped is stopped, ch_done moved into the terminal, ch_fresh was completed by an
    # operator; ch_live is untouched.
    add("chunk_stopped", TEN_A, "ch_stopped", stopped_at=_T1)
    add("chunk_completed", TEN_A, "ch_fresh", completed_at=_T1)
    add("transitions", TEN_A, "ch_done", transition_id="tr_a", to_node_id=RESERVED_TERMINAL, recorded_at=_T1, epoch=1)
    # Tenant B, the same ids: ch_live is stopped, ch_stopped completed after A's stop, and ch_done's
    # terminal transition is superseded by a newer movement and a migration; ch_fresh is untouched.
    add("chunk_stopped", TEN_B, "ch_live", stopped_at=_T1)
    add("chunk_completed", TEN_B, "ch_stopped", completed_at=_T2)
    add("transitions", TEN_B, "ch_done", transition_id="tr_b1", to_node_id=RESERVED_TERMINAL, recorded_at=_T1, epoch=1)
    add("transitions", TEN_B, "ch_done", transition_id="tr_b2", to_node_id="nd_work", recorded_at=_T2, epoch=1)
    add("chunk_migrations", TEN_B, "ch_done", recorded_at=_T2, epoch=1)
    add("chunk_restarts", TEN_B, "ch_done", recorded_at=_T2, epoch=1)
    add("transitions", TEN_B, "ch_live", transition_id="tr_b3", to_node_id=RESERVED_TERMINAL, recorded_at=_T1, epoch=1)
    # Ephemeral: A grouped one chunk away, B deleted another.
    add("chunk_grouped", TEN_A, "ch_gone_a")
    add("chunk_deleted", TEN_B, "ch_gone_b")
    # One question id in both tenants: only B's is answered and delivered.
    add("questions", TEN_A, "ch_live", question_id="qn_1")
    add("questions", TEN_B, "ch_live", question_id="qn_1")
    add("question_answers", TEN_B, "ch_live", question_id="qn_1", answer="yes", answered_by="op", answered_at=_T1)
    add("answer_deliveries", TEN_B, "ch_live", question_id="qn_1", delivered_at=_T2)


@pytest.fixture
def stand_in_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """A store of the stand-in tables, seeded for two tenants, with the shared builders reading them
    in place of the live schema's."""
    for name, table in _STAND_INS.items():
        monkeypatch.setattr(s, name, table)
    monkeypatch.setattr(chunk_rows, "_EPHEMERAL_TABLES", (_STAND_INS["chunk_grouped"], _STAND_INS["chunk_deleted"]))
    engine = create_engine(f"sqlite:///{tmp_path / 'stand_in.db'}")
    _md.create_all(engine)
    with engine.begin() as conn:
        _seed(conn)
    yield engine
    engine.dispose()


def _scoped(engine: Engine, tenant: TenantId) -> ScopedConnections:
    return ScopedConnections(engine, HubStoreErrorFactory(get_logger("test")), StoreScope(tenant))


def _chunk_ids(connections: ScopedConnections, predicate: ColumnElement[bool]) -> set[str]:
    chunks = _STAND_INS["chunks"]
    with connections.read("test") as conn:
        return set(conn.execute(connections.select(chunks, chunks.c.chunk_id).where(predicate)).scalars())


@pytest.mark.parametrize(
    ("tenant", "live", "stopped", "done"),
    [
        (TEN_A, {"ch_live"}, {"ch_stopped"}, {"ch_done", "ch_fresh"}),
        (TEN_B, {"ch_done", "ch_fresh"}, {"ch_live"}, {"ch_stopped"}),
    ],
    ids=["tenant_a", "tenant_b"],
)
def test_each_terminal_predicate_reads_only_its_scope_facts(
    stand_in_engine: Engine, tenant: TenantId, live: set[str], stopped: set[str], done: set[str]
) -> None:
    connections = _scoped(stand_in_engine, tenant)
    assert _chunk_ids(connections, maybe_live(connections)) == live
    assert _chunk_ids(connections, settled_stopped(connections)) == stopped
    assert _chunk_ids(connections, settled_done(connections)) == done
    assert _chunk_ids(connections, settled_done(connections, finished_before=_T2 + timedelta(days=1))) == done


def test_the_fence_terminal_read_without_a_from_reads_only_its_scope(stand_in_engine: Engine) -> None:
    with _scoped(stand_in_engine, TEN_A).read("test") as conn:
        assert chunk_rows._is_terminal(conn, "ch_stopped") is True
        assert chunk_rows._is_terminal(conn, "ch_live") is False
    with _scoped(stand_in_engine, TEN_B).read("test") as conn:
        assert chunk_rows._is_terminal(conn, "ch_live") is True
        assert chunk_rows._is_terminal(conn, "ch_done") is False
        assert chunk_rows._is_terminal(conn, "ch_fresh") is False


def test_each_ephemeral_union_arm_is_scoped(stand_in_engine: Engine) -> None:
    chunks = _STAND_INS["chunks"]
    for tenant, gone in ((TEN_A, "ch_gone_a"), (TEN_B, "ch_gone_b")):
        connections = _scoped(stand_in_engine, tenant)
        with connections.read("test") as conn:
            assert set(conn.execute(ephemeral_ids_select(connections)).scalars()) == {gone}
            assert chunk_rows.ephemeral_ids(conn) == {gone}
        assert _chunk_ids(connections, chunks.c.chunk_id.not_in(ephemeral_ids_select(connections))) == {
            "ch_live",
            "ch_stopped",
            "ch_done",
            "ch_fresh",
        }


def test_the_question_read_keeps_its_outer_joins_outer_within_the_scope(stand_in_engine: Engine) -> None:
    a, b = _scoped(stand_in_engine, TEN_A), _scoped(stand_in_engine, TEN_B)
    with a.read("test") as conn:
        (row_a,) = conn.execute(QUESTIONS.select(a)).all()
    with b.read("test") as conn:
        (row_b,) = conn.execute(QUESTIONS.select(b)).all()
    assert (row_a.tenant_id, row_a.answered_by, row_a.delivered_at) == (TEN_A, None, None)
    assert (row_b.tenant_id, row_b.answered_by) == (TEN_B, "op")
    assert row_b.delivered_at is not None
