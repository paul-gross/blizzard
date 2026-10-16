"""The tenant-deletion closed-at revision: every deletion fact gains ``closed_at`` — its ``deleted_at`` when its deletion
completed, the time the migration ran when it is underway — paged in bounded batches, then required; the plain tenant
index becomes a unique constraint, refused while two facts name one tenant; a re-run changes nothing; and the downgrade
refuses while a deletion is underway, else restores the store exactly."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.script import ScriptDirectory
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.foundation.store.utc import as_utc
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import Migrations
from blizzard.hub.store import MIGRATIONS_DIR
from blizzard.hub.store import schema as s
from tests.test_store_migrations import _schema_snapshot

pytestmark = [pytest.mark.component, pytest.mark.migration_revision("20261008_1720_tenant_deletion_closed_at")]

_INVITATION = "20261008_1710_auth_state_invitation"
_CLOSED_AT = "20261008_1720_tenant_deletion_closed_at"
_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_NOW = datetime(2026, 10, 8, 17, 20, tzinfo=UTC)
_INSERT = sa.text(
    "INSERT INTO tenant_deletions (tenant_id, name, deleted_at, deleted_by, rows_removed)"
    " VALUES (:tenant, :tenant, :deleted_at, 'usr_test', :rows)"
)


def _batch_rows() -> int:
    revision = ScriptDirectory(str(MIGRATIONS_DIR)).get_revision(_CLOSED_AT)
    assert revision is not None
    return revision.module._BATCH_ROWS


@contextmanager
def _at_the_invitation(tmp_path: Path) -> Iterator[tuple[MigrationRunner, sa.Engine]]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = Migrations(HubConfig(root=tmp_path, db_url=db_url), FixedClock(_NOW)).runner
    runner.upgrade(_INVITATION)
    engine = create_engine_from_url(db_url)
    try:
        yield runner, engine
    finally:
        engine.dispose()


def _seed(engine: sa.Engine, facts: list[tuple[str, datetime | None]]) -> None:
    with engine.begin() as conn:
        conn.execute(
            _INSERT,
            [{"tenant": t, "deleted_at": at, "rows": None if at is None else 3} for t, at in facts],
        )


def _closed_at(engine: sa.Engine) -> dict[str, datetime]:
    with engine.connect() as conn:
        rows = conn.execute(sa.select(s.tenant_deletions.c.tenant_id, s.tenant_deletions.c.closed_at)).all()
    return {row.tenant_id: as_utc(row.closed_at) for row in rows}


def _shape(engine: sa.Engine) -> tuple[dict[str, bool], set[str], set[str]]:
    inspector = sa.inspect(engine)
    return (
        {c["name"]: bool(c["nullable"]) for c in inspector.get_columns("tenant_deletions")},
        {str(i["name"]) for i in inspector.get_indexes("tenant_deletions")},
        {str(u["name"]) for u in inspector.get_unique_constraints("tenant_deletions")},
    )


def test_every_fact_closes_when_it_was_deleted_or_now_while_underway_and_the_column_is_required(
    tmp_path: Path,
) -> None:
    with _at_the_invitation(tmp_path) as (runner, engine):
        _seed(engine, [("ten_done", _T0), ("ten_underway", None)])

        runner.upgrade(_CLOSED_AT)

        assert _closed_at(engine) == {"ten_done": _T0, "ten_underway": _NOW}
        columns, indexes, uniques = _shape(engine)
        assert columns == {c.name: c.nullable for c in s.tenant_deletions.columns}
        assert columns["closed_at"] is False
        assert "ix_tenant_deletions_tenant_id" not in indexes
        assert uniques == {"uq_tenant_deletions_tenant_id"}
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO tenant_deletions (tenant_id, name, closed_at, deleted_by)"
                    " VALUES ('ten_done', 'again', :at, 'usr_test')"
                ),
                {"at": _NOW},
            )


def test_the_fill_pages_past_one_batch_and_misses_no_fact(tmp_path: Path) -> None:
    batch = _batch_rows()
    with _at_the_invitation(tmp_path) as (runner, engine):
        facts = [(f"ten_{n:05d}", _T0 + timedelta(seconds=n) if n % 2 else None) for n in range(2 * batch + 1)]
        _seed(engine, facts)

        runner.upgrade(_CLOSED_AT)

        assert _closed_at(engine) == {t: at or _NOW for t, at in facts}


def test_the_upgrade_refuses_while_two_facts_name_one_tenant(tmp_path: Path) -> None:
    with _at_the_invitation(tmp_path) as (runner, engine):
        _seed(engine, [("ten_twice", _T0), ("ten_twice", None), ("ten_once", _T0)])

        with pytest.raises(RuntimeError, match="ten_twice") as refused:
            runner.upgrade(_CLOSED_AT)

        assert "ten_once" not in str(refused.value)
        assert "closed_at" not in _shape(engine)[0]


def test_a_rerun_over_a_store_already_reshaped_changes_nothing(tmp_path: Path) -> None:
    with _at_the_invitation(tmp_path) as (runner, engine):
        _seed(engine, [("ten_done", _T0)])
        runner.upgrade(_CLOSED_AT)
        schema = _schema_snapshot(engine, exemptions={}, name_exemptions={})
        with engine.begin() as conn:
            conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_INVITATION}'"))

        runner.upgrade(_CLOSED_AT)

        assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema
        assert _closed_at(engine) == {"ten_done": _T0}


def test_a_rerun_after_a_stop_before_the_tighten_finishes_the_job(tmp_path: Path) -> None:
    with _at_the_invitation(tmp_path) as (runner, engine):
        _seed(engine, [("ten_done", _T0)])
        with engine.begin() as conn:
            conn.execute(sa.text("ALTER TABLE tenant_deletions ADD COLUMN closed_at DATETIME"))

        runner.upgrade(_CLOSED_AT)

        assert _closed_at(engine) == {"ten_done": _T0}
        assert _shape(engine)[0]["closed_at"] is False
        assert _shape(engine)[2] == {"uq_tenant_deletions_tenant_id"}


def test_the_downgrade_refuses_while_a_deletion_is_underway(tmp_path: Path) -> None:
    with _at_the_invitation(tmp_path) as (runner, engine):
        _seed(engine, [("ten_done", _T0), ("ten_underway", None)])
        runner.upgrade(_CLOSED_AT)

        with pytest.raises(RuntimeError, match="ten_underway") as refused:
            runner.downgrade(_INVITATION)

        assert "ten_done" not in str(refused.value)
        assert _shape(engine)[0]["closed_at"] is False


def test_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    with _at_the_invitation(tmp_path) as (runner, engine):
        schema = _schema_snapshot(engine, exemptions={}, name_exemptions={})
        _seed(engine, [("ten_done", _T0)])
        runner.upgrade(_CLOSED_AT)

        runner.downgrade(_INVITATION)

        assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema
        with engine.connect() as conn:
            rows = conn.execute(sa.text("SELECT tenant_id, rows_removed FROM tenant_deletions")).all()
        assert [tuple(row) for row in rows] == [("ten_done", 3)]
