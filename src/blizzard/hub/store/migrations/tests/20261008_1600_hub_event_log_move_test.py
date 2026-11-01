"""The hub event log move: the hub's own export occurrences leave the tenant's ``event_log`` for ``hub_event_log`` with
their ids, a runner's report of its own export and every tenant occurrence staying; a re-run over a stopped move copies
nothing twice, while an event whose id the hub log gives another event takes a fresh one; the move pages in bounded
batches; the downgrade, with one tenant only, moves them back exactly; and a downgrade then upgrade keeps every export
event once."""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.script import ScriptDirectory
from sqlalchemy import event
from sqlalchemy.engine.interfaces import DBAPICursor, ExecutionContext

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import MIGRATIONS_DIR
from tests.migration_support_store_migrations import schema_snapshot

pytestmark = [pytest.mark.component]

_SURROGATE = "20261008_1500_scope_surrogate"

_MOVE = "20261008_1600_hub_event_log_move"

_SECOND_TENANT = "ten_01J9Z3M0P8QK7V2S4W6X8Y0A1C"

_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_KIND_INDEX = "ix_hub_event_log_kind_recorded_at"

_EXPORT_KINDS = (
    "trace-export-failed",
    "trace-export-recovered",
    "trace-window-skipped",
    "trace-config-rejected",
    "egress-write-failed",
    "egress-write-recovered",
    "egress-config-rejected",
    "egress-cursor-reset",
)

_INSERT = sa.text(
    "INSERT INTO event_log (id, recorded_at, severity, kind, runner_id, lease_id, message, detail, tenant_id)"
    " VALUES (:id, :at, :severity, :kind, :runner_id, :lease_id, :message, :detail, :tenant)"
)


def _batch_rows() -> int:
    revision = ScriptDirectory(str(MIGRATIONS_DIR)).get_revision(_MOVE)
    assert revision is not None
    return revision.module._BATCH_ROWS


def _at_the_surrogate(tmp_path: Path) -> tuple[MigrationRunner, sa.Engine, str]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_SURROGATE)
    engine = create_engine_from_url(db_url)
    with engine.connect() as conn:
        [tenant] = conn.execute(sa.text("SELECT tenant_id FROM tenants")).scalars().all()
    return runner, engine, tenant


def _event(row_id: int, kind: str, *, runner_id: str | None = None, lease_id: str | None = None) -> dict[str, Any]:
    return {
        "id": row_id,
        "at": _T0 + timedelta(seconds=row_id),
        "severity": "warning",
        "kind": kind,
        "runner_id": runner_id,
        "lease_id": lease_id,
        "message": f"m{row_id}",
        "detail": f'{{"n": {row_id}}}',
    }


def _seed(engine: sa.Engine, tenant: str) -> tuple[set[int], set[int]]:
    """One hub row of every export kind, a runner's own trace report, an export kind naming a lease, and a tenant
    occurrence. Returns the ids that move and the ids that stay."""
    moving = [_event(n, kind) for n, kind in enumerate(_EXPORT_KINDS, start=1)]
    staying = [
        _event(20, "trace-export-failed", runner_id="run_1"),
        _event(21, "egress-write-failed", lease_id="ls_1"),
        _event(22, "attempt-failed", runner_id="run_1"),
    ]
    with engine.begin() as conn:
        conn.execute(_INSERT, [{**row, "tenant": tenant} for row in (*moving, *staying)])
    return {row["id"] for row in moving}, {row["id"] for row in staying}


def _rows(engine: sa.Engine, table: str) -> list[tuple[Any, ...]]:
    columns = "id, recorded_at, severity, kind, message, detail"
    with engine.connect() as conn:
        return [tuple(row) for row in conn.execute(sa.text(f"SELECT {columns} FROM {table} ORDER BY id"))]


def _hub_event(row_id: int | None, kind: str, message: str) -> dict[str, Any]:
    return {"id": row_id, "at": _T0 + timedelta(minutes=1), "kind": kind, "message": message}


_HUB_INSERT = sa.text(
    "INSERT INTO hub_event_log (id, recorded_at, severity, kind, message, detail)"
    " VALUES (:id, :at, 'info', :kind, :message, NULL)"
)


def _messages_by_kind(engine: sa.Engine, table: str) -> list[tuple[str, str]]:
    with engine.connect() as conn:
        return sorted((row.kind, row.message) for row in conn.execute(sa.text(f"SELECT kind, message FROM {table}")))


def _event_log(engine: sa.Engine) -> list[tuple[Any, ...]]:
    with engine.connect() as conn:
        return [tuple(row) for row in conn.execute(sa.text("SELECT * FROM event_log ORDER BY id"))]


def _ids(engine: sa.Engine, table: str) -> set[int]:
    return {row[0] for row in _rows(engine, table)}


def _index_names(engine: sa.Engine, table: str) -> set[str]:
    return {str(index["name"]) for index in sa.inspect(engine).get_indexes(table)}


@contextmanager
def _rows_each_delete_removed() -> Generator[list[tuple[str, int]]]:
    """Every ``DELETE`` any engine runs while the context is open, as the table it names and the rows it removed."""
    deletes: list[tuple[str, int]] = []

    def record(
        conn: sa.Connection,
        cursor: DBAPICursor,
        statement: str,
        parameters: object,
        context: ExecutionContext | None,
        executemany: bool,
    ) -> None:
        words = statement.split()
        if words[0].upper() == "DELETE":
            deletes.append((words[2], cursor.rowcount))

    event.listen(sa.Engine, "after_cursor_execute", record)
    try:
        yield deletes
    finally:
        event.remove(sa.Engine, "after_cursor_execute", record)


def test_the_upgrade_moves_every_hub_export_event_with_its_id_and_leaves_the_rest(tmp_path: Path) -> None:
    runner, engine, tenant = _at_the_surrogate(tmp_path)
    try:
        moving, staying = _seed(engine, tenant)
        before = {row[0]: row for row in _rows(engine, "event_log")}

        runner.upgrade(_MOVE)

        assert _rows(engine, "hub_event_log") == [before[row_id] for row_id in sorted(moving)]
        assert _ids(engine, "event_log") == staying
        assert _KIND_INDEX in _index_names(engine, "hub_event_log")
    finally:
        engine.dispose()


def test_a_rerun_over_a_stopped_move_copies_nothing_twice(tmp_path: Path) -> None:
    runner, engine, tenant = _at_the_surrogate(tmp_path)
    try:
        moving, staying = _seed(engine, tenant)
        before = {row[0]: row for row in _rows(engine, "event_log")}
        # A run stopped part way: one page's rows copied but not yet deleted, the one before it already gone.
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO hub_event_log (id, recorded_at, severity, kind, message, detail)"
                    " SELECT id, recorded_at, severity, kind, message, detail FROM event_log WHERE id IN (1, 2, 3)"
                )
            )
            conn.execute(sa.text("DELETE FROM event_log WHERE id IN (1, 2)"))

        runner.upgrade(_MOVE)

        assert _rows(engine, "hub_event_log") == [before[row_id] for row_id in sorted(moving)]
        assert _ids(engine, "event_log") == staying
    finally:
        engine.dispose()


def test_a_rerun_copies_an_event_whose_id_the_hub_log_gives_another_event_under_a_fresh_id(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    runner, engine, tenant = _at_the_surrogate(tmp_path)
    try:
        moving, _ = _seed(engine, tenant)
        before = {row[0]: row for row in _rows(engine, "event_log")}
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO hub_event_log (id, recorded_at, severity, kind, message, detail)"
                    " SELECT id, recorded_at, severity, kind, message, detail FROM event_log WHERE id = 1"
                )
            )
            conn.execute(_HUB_INSERT, _hub_event(2, "hub-started", "started"))
        caplog.set_level(logging.INFO, logger="alembic.runtime.migration")

        runner.upgrade(_MOVE)

        rows = _rows(engine, "hub_event_log")
        assert [row for row in rows if row[0] in moving - {2}] == [before[row_id] for row_id in sorted(moving - {2})]
        assert [row[3] for row in rows if row[0] == 2] == ["hub-started"]
        [reissued] = [row for row in rows if row[4] == "m2"]
        assert reissued[0] not in moving and reissued[1:] == before[2][1:]
        assert "1 hub export events took fresh hub_event_log ids" in caplog.text
    finally:
        engine.dispose()


def test_a_downgrade_then_upgrade_keeps_every_export_event_once(tmp_path: Path) -> None:
    runner, engine, tenant = _at_the_surrogate(tmp_path)

    def unnumbered(n: int, kind: str, **names: str) -> dict[str, Any]:
        return {**_event(n, kind, **names), "id": None, "tenant": tenant}

    try:
        with engine.begin() as conn:
            conn.execute(_INSERT, [unnumbered(n, "attempt-failed", runner_id="run_1") for n in (1, 2)])
            conn.execute(_INSERT, [unnumbered(n, "trace-export-failed") for n in (3, 4, 5)])
        runner.upgrade(_MOVE)
        # At head the hub records its own occurrences, and the tenant's log reuses the moved events' ids, so the
        # downgrade gives two of them fresh ids — the ids the hub's own occurrences hold.
        with engine.begin() as conn:
            conn.execute(
                _HUB_INSERT, [_hub_event(None, "hub-migrated", "migrated"), _hub_event(None, "hub-started", "s")]
            )
            conn.execute(_INSERT, [unnumbered(n, "attempt-failed", runner_id="run_1") for n in (6, 7)])
        runner.downgrade(_SURROGATE)
        # Below the move the hub records one more export into the tenant's log.
        with engine.begin() as conn:
            conn.execute(_INSERT, unnumbered(8, "egress-write-failed"))

        runner.upgrade(_MOVE)

        assert _messages_by_kind(engine, "hub_event_log") == [
            ("egress-write-failed", "m8"),
            ("hub-migrated", "migrated"),
            ("hub-started", "s"),
            *(("trace-export-failed", f"m{n}") for n in (3, 4, 5)),
        ]
        assert _messages_by_kind(engine, "event_log") == [("attempt-failed", f"m{n}") for n in (1, 2, 6, 7)]
    finally:
        engine.dispose()


def test_the_move_and_its_reverse_each_page_in_bounded_batches(tmp_path: Path) -> None:
    runner, engine, tenant = _at_the_surrogate(tmp_path)
    batch = _batch_rows()
    count = 2 * batch + 7
    try:
        with engine.begin() as conn:
            conn.execute(_INSERT, [{**_event(n, "egress-write-failed"), "tenant": tenant} for n in range(1, count + 1)])

        with _rows_each_delete_removed() as deletes:
            runner.upgrade(_MOVE)
        moved = [rows for table, rows in deletes if table == "event_log"]
        assert len(moved) == 3 and max(moved) <= batch and sum(moved) == count
        assert len(_ids(engine, "hub_event_log")) == count

        with _rows_each_delete_removed() as deletes:
            runner.downgrade(_SURROGATE)
        returned = [rows for table, rows in deletes if table == "hub_event_log"]
        assert len(returned) == 3 and max(returned) <= batch and sum(returned) == count
        assert _ids(engine, "event_log") == set(range(1, count + 1))
    finally:
        engine.dispose()


def test_the_downgrade_refuses_while_a_second_tenant_exists_naming_both(tmp_path: Path) -> None:
    runner, engine, tenant = _at_the_surrogate(tmp_path)
    try:
        runner.upgrade(_MOVE)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO tenants (tenant_id, name, created_at, created_by) VALUES (:t, 'other', :at, 'usr_1')"
                ),
                {"t": _SECOND_TENANT, "at": _T0},
            )

        with pytest.raises(RuntimeError, match="delete every tenant but one before downgrading") as refused:
            runner.downgrade(_SURROGATE)

        assert tenant in str(refused.value) and _SECOND_TENANT in str(refused.value)
    finally:
        engine.dispose()


def test_with_one_tenant_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    runner, engine, tenant = _at_the_surrogate(tmp_path)
    try:
        _seed(engine, tenant)
        rows = _event_log(engine)
        schema = schema_snapshot(engine, exemptions={}, name_exemptions={})

        runner.upgrade(_MOVE)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO hub_event_log (recorded_at, severity, kind, message) VALUES (:at, 'info', 'hub-started', 's')"
                ),
                {"at": _T0},
            )
        runner.downgrade(_SURROGATE)

        assert _event_log(engine) == rows
        assert [row[3] for row in _rows(engine, "hub_event_log")] == ["hub-started"]
        assert schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema
    finally:
        engine.dispose()


def test_a_moved_back_event_whose_id_was_reused_takes_a_fresh_one(tmp_path: Path) -> None:
    runner, engine, tenant = _at_the_surrogate(tmp_path)
    try:
        moving, _ = _seed(engine, tenant)
        runner.upgrade(_MOVE)
        with engine.begin() as conn:
            conn.execute(
                _INSERT, {**_event(1, "attempt-failed", runner_id="run_2"), "message": "reused", "tenant": tenant}
            )

        runner.downgrade(_SURROGATE)

        with engine.connect() as conn:
            rows = conn.execute(sa.text("SELECT id, kind, message, tenant_id, runner_id FROM event_log")).all()
        by_message = {row.message: row for row in rows}
        assert by_message["m1"].id not in moving | {20, 21, 22} and by_message["m1"].kind == "trace-export-failed"
        assert (by_message["m1"].tenant_id, by_message["m1"].runner_id) == (tenant, None)
        assert {by_message[f"m{n}"].id for n in moving - {1}} == moving - {1}
    finally:
        engine.dispose()
