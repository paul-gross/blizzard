"""The hub-settings revision preserves tenant data across an upgrade and downgrade."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.hub.store.schema import hub_setting_changes, tenants
from tests.support.migrations.store_migrations import schema_snapshot
from tests.support.scaffolding import add_tenant, migrate_to

pytestmark = pytest.mark.component

_PARENT = "20261111_1000_chunk_repository_targets"
_REVISION = "20261112_0900_hub_settings"


def test_the_downgrade_restores_the_parent_store_and_keeps_tenant_data(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    tenant = add_tenant(engine, "retained")
    before = schema_snapshot(engine, exemptions={}, name_exemptions={})
    runner.upgrade(_REVISION)

    runner.downgrade(_PARENT)

    assert schema_snapshot(engine, exemptions={}, name_exemptions={}) == before
    runner.upgrade(_REVISION)
    with engine.connect() as conn:
        assert conn.execute(sa.select(tenants.c.name).where(tenants.c.tenant_id == tenant)).scalar_one() == "retained"
        assert conn.execute(sa.select(sa.func.count()).select_from(hub_setting_changes)).scalar_one() == 0
