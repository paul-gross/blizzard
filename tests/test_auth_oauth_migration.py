"""The provider-login migration — ``auth_state``/``auth_facts`` (component tier,
issue #92).

Mirrors ``test_auth_identity_migration.py``'s own shape: a fresh ``base -> head`` store
is covered generically by ``test_store_migrations.py``'s whole-ladder test; this covers
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

_BEFORE = "20260721_1100_hub_auth_identity_spine"  # the head just before this revision


def _upgrade_existing_store_to_head(tmp_path: Path):  # type: ignore[no-untyped-def]
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    runner.upgrade("head")
    return create_engine_from_url(db_url)


def test_tables_and_columns_exist_after_upgrading_an_existing_store(tmp_path: Path) -> None:
    engine = _upgrade_existing_store_to_head(tmp_path)
    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"auth_state", "auth_facts"} <= tables

    state_columns = {c["name"] for c in inspector.get_columns("auth_state")}
    assert state_columns == {
        "state",
        "kind",
        "provider_name",
        "return_to",
        "code_challenge",
        "created_at",
        "expires_at",
    }

    facts_columns = {c["name"] for c in inspector.get_columns("auth_facts")}
    assert facts_columns == {"id", "kind", "actor", "subject", "detail", "recorded_at"}


def test_downgrade_removes_the_oauth_tables(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade("head")
    runner.downgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    tables = set(sa.inspect(engine).get_table_names())
    assert not ({"auth_state", "auth_facts"} & tables)
