"""The superuser-bootstrap migration — ``superuser_bootstrap`` (component tier, issue
#94).

Mirrors ``test_auth_oauth_migration.py``'s own shape: a fresh ``base -> head`` store is
covered generically by ``test_store_migrations.py``'s whole-ladder test; this covers
the "existing store, upgraded onward" half plus the downgrade.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner

pytestmark = pytest.mark.component

_BEFORE = "20260721_1200_hub_auth_oauth"  # the head just before this revision


def _upgrade_existing_store_to_head(tmp_path: Path):  # type: ignore[no-untyped-def]
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    runner.upgrade("head")
    return create_engine_from_url(db_url)


def test_table_and_columns_exist_after_upgrading_an_existing_store(tmp_path: Path) -> None:
    engine = _upgrade_existing_store_to_head(tmp_path)
    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    assert "superuser_bootstrap" in tables

    columns = {c["name"] for c in inspector.get_columns("superuser_bootstrap")}
    assert columns == {"id", "email", "claimed_user_id", "updated_at"}


def test_downgrade_removes_the_bootstrap_table(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade("head")
    runner.downgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    tables = set(sa.inspect(engine).get_table_names())
    assert "superuser_bootstrap" not in tables
