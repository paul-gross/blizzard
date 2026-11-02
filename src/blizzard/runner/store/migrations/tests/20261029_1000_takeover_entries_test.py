"""The takeover-entries revision marks every takeover open at upgrade time as entered, at its own opening instant, so a
person's takeover opened under the prior release is left alone by startup recovery; an ended takeover gets no entry;
the downgrade drops the table and a re-upgrade backfills again; a rerun changes nothing.

Stores are seeded from the parent revision's tables, never from ``src/blizzard/runner/store/schema.py``."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner import runtime as runner_runtime
from tests.config_inputs import init_host

pytestmark = [pytest.mark.component]

_PARENT = "20261026_1000_fact_append_sequence"
_REVISION = "20261029_1000_takeover_entries"

_T0 = "2026-01-01 00:00:00.000000"
_T1 = "2026-01-01 00:00:01.000000"


def _insert(conn: sa.Connection, table: str, **values: object) -> None:
    columns = ", ".join(values)
    binds = ", ".join(f":{name}" for name in values)
    conn.execute(sa.text(f"INSERT INTO {table} ({columns}) VALUES ({binds})"), values)


def _store_at_parent(tmp_path: Path) -> tuple[MigrationRunner, sa.Engine]:
    config = init_host(tmp_path)
    migration = runner_runtime.migration_runner(config)
    migration.downgrade(_PARENT)
    engine = create_engine_from_url(config.db_url)
    with engine.begin() as conn:
        _insert(conn, "runner_identity", runner_id="rn_a", runner_name="rn_a", registered_at=_T0)
        for takeover, at in (("tko_open", _T0), ("tko_ended", _T1)):
            _insert(
                conn,
                "takeovers",
                takeover_id=takeover,
                chunk_id="ch",
                workdir="/w",
                opened_at=at,
                runner_id="rn_a",
            )
        _insert(conn, "takeover_ends", takeover_id="tko_ended", ended_at=_T1, runner_id="rn_a")
    return migration, engine


def _entries(engine: sa.Engine) -> list[tuple[str, str]]:
    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT takeover_id, entered_at FROM takeover_entries ORDER BY takeover_id")).all()
    return [(r[0], str(r[1])) for r in rows]


def test_a_takeover_open_at_upgrade_reads_as_entered_and_an_ended_one_gets_no_entry(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        migration.upgrade(_REVISION)

        assert _entries(engine) == [("tko_open", _T0)]
    finally:
        engine.dispose()


def test_the_downgrade_drops_the_table_and_a_reupgrade_backfills_again(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        migration.upgrade(_REVISION)
        first = _entries(engine)

        migration.downgrade(_PARENT)
        assert migration.current_revision() == _PARENT
        assert "takeover_entries" not in sa.inspect(engine).get_table_names()

        migration.upgrade(_REVISION)
        assert _entries(engine) == first
    finally:
        engine.dispose()


def test_a_rerun_of_an_upgrade_that_already_ran_changes_nothing(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        migration.upgrade(_REVISION)
        before = _entries(engine)
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE alembic_version SET version_num = :parent"), {"parent": _PARENT})

        migration.upgrade(_REVISION)

        assert _entries(engine) == before
    finally:
        engine.dispose()
