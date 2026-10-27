"""The fact-append-sequence revision numbers every existing bind, release, park, resume, requeue and lease per runner
so that each closure reads as it did by instants — same-instant ties included — and seeds each runner's counter at its
greatest ``seq``; its downgrade drops the columns and the counter table and restores the parent's schema with every row
kept; a rerun of an upgrade that already ran changes nothing.

Stores are seeded from the parent revision's tables, never from ``src/blizzard/runner/store/schema.py``, whose tables
keep changing shape after this revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner import runtime as runner_runtime
from tests.config_inputs import init_host

pytestmark = [pytest.mark.component]

_PARENT = "20261023_1000_pause_park_cause"

_REVISION = "20261026_1000_fact_append_sequence"

_RUNNERS = ("rn_a", "rn_b")

_T0 = "2026-01-01 00:00:00.000000"
_T1 = "2026-01-01 00:00:01.000000"
_T2 = "2026-01-01 00:00:02.000000"

_SEQUENCED = ("env_bindings", "binding_releases", "pause_parks", "pause_park_resumes", "requeues", "leases")

#: Each stream's current state by comparing instants, as the parent revision reads it, and by ``seq``, as this one does:
#: the ids of the held bindings, the open parks, and the pending requeues.
_BY_INSTANT = {
    "held": "SELECT b.runner_id, b.id FROM env_bindings b WHERE NOT EXISTS (SELECT 1 FROM binding_releases r"
    " WHERE r.runner_id = b.runner_id AND r.chunk_id = b.chunk_id AND r.environment_id = b.environment_id"
    " AND r.released_at >= b.bound_at)",
    "parks": "SELECT p.runner_id, p.id FROM pause_parks p WHERE NOT EXISTS (SELECT 1 FROM pause_park_resumes r"
    " WHERE r.runner_id = p.runner_id AND r.lease_id = p.lease_id AND r.resumed_at >= p.parked_at)",
    "requeues": "SELECT q.runner_id, q.id FROM requeues q WHERE NOT EXISTS (SELECT 1 FROM leases l"
    " WHERE l.runner_id = q.runner_id AND l.chunk_id = q.chunk_id AND l.created_at >= q.requeued_at)",
}
_BY_SEQ = {
    name: sql.replace("released_at >= b.bound_at", "seq > b.seq")
    .replace("resumed_at >= p.parked_at", "seq > p.seq")
    .replace("created_at >= q.requeued_at", "seq > q.seq")
    for name, sql in _BY_INSTANT.items()
}


def _insert(conn: sa.Connection, table: str, **values: object) -> None:
    columns = ", ".join(values)
    binds = ", ".join(f":{name}" for name in values)
    conn.execute(sa.text(f"INSERT INTO {table} ({columns}) VALUES ({binds})"), values)


def _seed(conn: sa.Connection, runner: str) -> None:
    """Every closure pair, tied and not, in one runner: a bind and its release at one instant, a rebind after them; a
    park and its resume at one instant, a second park; a requeue and the mint at its instant, a requeue after every
    mint."""

    def bind(chunk: str, env: str, at: str) -> None:
        _insert(conn, "env_bindings", chunk_id=chunk, environment_id=env, workdir="/w", bound_at=at, runner_id=runner)

    def release(chunk: str, env: str, at: str) -> None:
        _insert(conn, "binding_releases", chunk_id=chunk, environment_id=env, released_at=at, runner_id=runner)

    def park(lease: str, at: str) -> None:
        _insert(conn, "pause_parks", lease_id=f"{runner}-{lease}", chunk_id="ch", parked_at=at, runner_id=runner)

    def resume(lease: str, at: str) -> None:
        _insert(conn, "pause_park_resumes", lease_id=f"{runner}-{lease}", resumed_at=at, runner_id=runner)

    def mint(lease: str, chunk: str, at: str) -> None:
        _insert(conn, "leases", lease_id=f"{runner}-{lease}", chunk_id=chunk, epoch=1, created_at=at, runner_id=runner)

    def requeue(chunk: str, at: str) -> None:
        _insert(conn, "requeues", chunk_id=chunk, requeued_at=at, runner_id=runner)

    # Rows are written in an order unlike their numbering: later instants first, a closer before its opener.
    release("c1", "e1", _T0)
    bind("c1", "e1", _T2)  # held: after its release
    bind("c1", "e1", _T0)  # released: its release is at its own instant
    bind("c2", "e2", _T1)  # held: no release
    resume("l1", _T1)
    park("l1", _T2)  # open: after its resume
    park("l1", _T1)  # closed: its resume is at its own instant
    park("l2", _T0)  # open: no resume
    mint("m1", "c3", _T1)
    requeue("c3", _T1)  # consumed: the mint is at its own instant
    requeue("c4", _T2)  # pending: no mint at or after it
    mint("m2", "c4", _T1)


def _store_at_parent(tmp_path: Path) -> tuple[MigrationRunner, sa.Engine]:
    config = init_host(tmp_path)
    migration = runner_runtime.migration_runner(config)
    migration.downgrade(_PARENT)
    engine = create_engine_from_url(config.db_url)
    with engine.begin() as conn:
        for runner in _RUNNERS:
            _insert(conn, "runner_identity", runner_id=runner, runner_name=runner, registered_at=_T0)
            _seed(conn, runner)
    return migration, engine


def _state(engine: sa.Engine, queries: dict[str, str]) -> dict[str, set[tuple[str, int]]]:
    with engine.connect() as conn:
        return {name: {(r[0], r[1]) for r in conn.execute(sa.text(sql))} for name, sql in queries.items()}


def _tables(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names()) - {"alembic_version"}


def _schema(engine: sa.Engine) -> dict[str, object]:
    inspector = sa.inspect(engine)
    return {
        table: (
            tuple((c["name"], str(c["type"]), c["nullable"]) for c in inspector.get_columns(table)),
            tuple(inspector.get_pk_constraint(table)["constrained_columns"]),
            frozenset((tuple(i["column_names"]), i["unique"], i["name"]) for i in inspector.get_indexes(table)),
        )
        for table in _tables(engine)
    }


def _contents(engine: sa.Engine) -> dict[str, list[tuple[object, ...]]]:
    with engine.connect() as conn:
        return {
            name: sorted(tuple(r) for r in conn.execute(sa.text(f"SELECT * FROM {name}")))
            for name in sorted(_tables(engine) - {"fact_counters"})
        }


def test_every_stream_reads_as_it_did_by_instants_ties_included(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        before = _state(engine, _BY_INSTANT)
        # Per runner: two of three bindings held, two of three parks open, one of two requeues pending.
        assert [len(before[name]) for name in ("held", "parks", "requeues")] == [4, 4, 2]

        migration.upgrade(_REVISION)

        after = _state(engine, _BY_SEQ)
        assert after == before
    finally:
        engine.dispose()


def test_rows_are_numbered_per_runner_and_each_counter_stands_at_its_greatest(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        migration.upgrade(_REVISION)

        with engine.connect() as conn:
            for runner in _RUNNERS:
                seqs = sorted(
                    seq
                    for table in _SEQUENCED
                    for seq in conn.execute(
                        sa.text(f"SELECT seq FROM {table} WHERE runner_id = :r"), {"r": runner}
                    ).scalars()
                )
                assert seqs == list(range(1, 13))
            counters = {r[0]: r[1] for r in conn.execute(sa.text("SELECT runner_id, last_seq FROM fact_counters"))}
        assert counters == {"rn_a": 12, "rn_b": 12}
    finally:
        engine.dispose()


def test_the_downgrade_drops_the_sequence_and_restores_the_parent_schema(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        before, contents = _schema(engine), _contents(engine)
        migration.upgrade(_REVISION)

        migration.downgrade(_PARENT)

        assert migration.current_revision() == _PARENT
        assert _schema(engine) == before
        assert _contents(engine) == contents
    finally:
        engine.dispose()


def test_a_rerun_of_an_upgrade_that_already_ran_changes_nothing(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        migration.upgrade(_REVISION)
        schema, contents = _schema(engine), _contents(engine)
        with engine.connect() as conn:
            counters = conn.execute(sa.text("SELECT * FROM fact_counters ORDER BY runner_id")).all()
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE alembic_version SET version_num = :parent"), {"parent": _PARENT})

        migration.upgrade(_REVISION)

        assert migration.current_revision() == _REVISION
        assert (_schema(engine), _contents(engine)) == (schema, contents)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT * FROM fact_counters ORDER BY runner_id")).all() == counters
    finally:
        engine.dispose()
