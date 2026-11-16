"""The egress ordering migration preserves history and is reversible and rerunnable."""

from pathlib import Path

import pytest
import sqlalchemy as sa

from tests.support.migrations.store_migrations import schema_snapshot
from tests.support.scaffolding import migrate_to

pytestmark = pytest.mark.component
_PARENT = "20261114_0900_app_credentials"
_REVISION = "20261116_0900_egress_order"


def test_upgrade_rerun_and_downgrade(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    before = schema_snapshot(engine, exemptions={}, name_exemptions={})
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO egress_cursor (id, dataset, usage_recorded_at, usage_id, row_count, files, recorded_at) VALUES "
                "(10, 'steps', '2026-01-01', 0, 0, '[]', '2026-02-01'), "
                "(20, 'steps', '2026-01-02', 0, 0, '[]', '2026-01-01'), "
                "(30, 'events', '2026-01-03', 0, 0, '[]', '2026-03-01')"
            )
        )
    runner.upgrade(_REVISION)
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT id, append_order FROM egress_cursor ORDER BY id")).all() == [
            (10, 0),
            (20, 0),
            (30, 0),
        ]
    with engine.begin() as conn:
        assert conn.execute(sa.text("SELECT id, append_order FROM egress_cursor ORDER BY id")).all() == [
            (10, 0),
            (20, 0),
            (30, 0),
        ]
        assert conn.execute(sa.text("SELECT count(*) FROM hub_locks WHERE name='egress_cursor'")).scalar_one() == 1
        assert (
            conn.execute(
                sa.text(
                    "SELECT id FROM egress_cursor WHERE dataset='steps' ORDER BY append_order DESC, recorded_at DESC, id DESC LIMIT 1"
                )
            ).scalar_one()
            == 10
        )
        plan = conn.execute(
            sa.text(
                "EXPLAIN QUERY PLAN SELECT id FROM egress_cursor WHERE dataset='steps' ORDER BY append_order DESC, recorded_at DESC, id DESC LIMIT 1"
            )
        ).all()
        assert any("ix_egress_cursor_dataset_append_order_recorded_at_id" in str(row) for row in plan)
        conn.execute(sa.text("UPDATE alembic_version SET version_num=:revision"), {"revision": _PARENT})
    runner.upgrade(_REVISION)
    runner.downgrade(_PARENT)
    assert schema_snapshot(engine, exemptions={}, name_exemptions={}) == before
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT id, dataset, recorded_at FROM egress_cursor ORDER BY id")).all() == [
            (10, "steps", "2026-02-01"),
            (20, "steps", "2026-01-01"),
            (30, "events", "2026-03-01"),
        ]
    runner.upgrade(_REVISION)
