"""The auth-state invitation revision: ``auth_state.invitation_token_hash`` is added nullable, as the schema declares
it, beside the rows already in flight; a re-run over a store that already holds the column changes nothing; and the
downgrade drops it, restoring the store exactly."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema as s
from tests.test_store_migrations import _schema_snapshot

pytestmark = [pytest.mark.component, pytest.mark.migration_revision("20261008_1710_auth_state_invitation")]

_MOVE = "20261008_1700_tenant_identity_move"
_INVITATION = "20261008_1710_auth_state_invitation"
_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_COLUMN = "invitation_token_hash"


@contextmanager
def _at_the_move(tmp_path: Path) -> Iterator[tuple[MigrationRunner, sa.Engine]]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_MOVE)
    engine = create_engine_from_url(db_url)
    try:
        yield runner, engine
    finally:
        engine.dispose()


def _columns(engine: sa.Engine) -> dict[str, bool]:
    return {c["name"]: c["nullable"] for c in sa.inspect(engine).get_columns("auth_state")}


def _seed_state(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO auth_state (state, kind, provider_name, return_to, created_at, expires_at)"
                " VALUES ('st_1', 'provider_login', 'github', '/', :at, :at)"
            ),
            {"at": _T0},
        )


def test_the_upgrade_adds_the_column_nullable_beside_the_rows_in_flight(tmp_path: Path) -> None:
    with _at_the_move(tmp_path) as (runner, engine):
        assert _COLUMN not in _columns(engine)
        _seed_state(engine)

        runner.upgrade(_INVITATION)

        assert _columns(engine)[_COLUMN] is True
        assert _columns(engine) == {c.name: c.nullable for c in s.auth_state.columns}
        with engine.connect() as conn:
            rows = conn.execute(sa.text(f"SELECT state, {_COLUMN} FROM auth_state")).all()
        assert [tuple(row) for row in rows] == [("st_1", None)]


def test_a_rerun_over_a_store_already_holding_the_column_changes_nothing(tmp_path: Path) -> None:
    with _at_the_move(tmp_path) as (runner, engine):
        with engine.begin() as conn:
            conn.execute(sa.text(f"ALTER TABLE auth_state ADD COLUMN {_COLUMN} VARCHAR"))
        schema = _schema_snapshot(engine, exemptions={}, name_exemptions={})

        runner.upgrade(_INVITATION)

        assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema


def test_the_downgrade_drops_the_column_and_restores_the_store_exactly(tmp_path: Path) -> None:
    with _at_the_move(tmp_path) as (runner, engine):
        schema = _schema_snapshot(engine, exemptions={}, name_exemptions={})
        runner.upgrade(_INVITATION)
        _seed_state(engine)
        with engine.begin() as conn:
            conn.execute(sa.text(f"UPDATE auth_state SET {_COLUMN} = 'abc'"))

        runner.downgrade(_MOVE)

        assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT state FROM auth_state")).scalars().all() == ["st_1"]
