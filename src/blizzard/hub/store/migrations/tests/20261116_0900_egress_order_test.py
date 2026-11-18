"""The egress ordering migration preserves history and is reversible and rerunnable."""

from pathlib import Path

import pytest
import sqlalchemy as sa

from tests.support.migrations.store_migrations import schema_snapshot
from tests.support.scaffolding import migrate_to

pytestmark = pytest.mark.component
_PARENT = "20261114_0900_app_credentials"
_REVISION = "20261116_0900_egress_order"
_COLUMNS = (
    "id, dataset, position_at, chunk_id, epoch, decision_id, usage_recorded_at, usage_id, "
    "row_count, files, recorded_at, segment_id, extractor_version, events_tenant_id"
)


def _assert_selected_checkpoints(conn: sa.Connection, *, upgraded: bool) -> None:
    expected = {
        "steps": (
            10,
            "2026-01-03",
            "chunk-a",
            2,
            "decision-a",
            "2026-01-04",
            41,
            12,
            '["steps-a.parquet"]',
            "2026-02-01",
        ),
        "events": (
            30,
            "2026-01-07",
            "chunk-c",
            4,
            "decision-c",
            "2026-01-08",
            43,
            36,
            '["events.parquet"]',
            "2026-03-01",
        ),
    }
    selections = [("", "recorded_at DESC, id DESC"), (" AND files != '[]'", "recorded_at DESC, id DESC")]
    if upgraded:
        selections.append(("", "append_order DESC, recorded_at DESC, id DESC"))
    for dataset, checkpoint in expected.items():
        for condition, ordering in selections:
            row = conn.execute(
                sa.text(
                    "SELECT id, position_at, chunk_id, epoch, decision_id, usage_recorded_at, usage_id, "
                    "row_count, files, recorded_at FROM egress_cursor WHERE dataset=:dataset "
                    f"{condition} ORDER BY {ordering} LIMIT 1"
                ),
                {"dataset": dataset},
            ).one()
            assert row == checkpoint


def test_upgrade_rerun_and_downgrade(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    before = schema_snapshot(engine, exemptions={}, name_exemptions={})
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                f"INSERT INTO egress_cursor ({_COLUMNS}) VALUES "
                "(10, 'steps', '2026-01-03', 'chunk-a', 2, 'decision-a', '2026-01-04', 41, "
                "12, '[\"steps-a.parquet\"]', '2026-02-01', 'segment-a', 'v1', 'tenant-a'), "
                "(20, 'steps', '2026-01-05', 'chunk-b', 3, 'decision-b', '2026-01-06', 42, "
                "24, '[\"steps-b.parquet\",\"steps-c.parquet\"]', '2026-01-01', 'segment-b', 'v2', 'tenant-b'), "
                "(30, 'events', '2026-01-07', 'chunk-c', 4, 'decision-c', '2026-01-08', 43, "
                "36, '[\"events.parquet\"]', '2026-03-01', 'segment-c', 'v3', 'tenant-c')"
            )
        )
        original = conn.execute(sa.text(f"SELECT {_COLUMNS} FROM egress_cursor ORDER BY id")).all()
        _assert_selected_checkpoints(conn, upgraded=False)
    runner.upgrade(_REVISION)
    with engine.connect() as conn:
        assert conn.execute(sa.text(f"SELECT {_COLUMNS} FROM egress_cursor ORDER BY id")).all() == original
        _assert_selected_checkpoints(conn, upgraded=True)
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
    with engine.connect() as conn:
        assert conn.execute(sa.text(f"SELECT {_COLUMNS} FROM egress_cursor ORDER BY id")).all() == original
        _assert_selected_checkpoints(conn, upgraded=True)
        assert conn.execute(sa.text("SELECT id, append_order FROM egress_cursor ORDER BY id")).all() == [
            (10, 0),
            (20, 0),
            (30, 0),
        ]
        assert conn.execute(sa.text("SELECT count(*) FROM hub_locks WHERE name='egress_cursor'")).scalar_one() == 1
    runner.downgrade(_PARENT)
    assert schema_snapshot(engine, exemptions={}, name_exemptions={}) == before
    with engine.connect() as conn:
        assert conn.execute(sa.text(f"SELECT {_COLUMNS} FROM egress_cursor ORDER BY id")).all() == original
        _assert_selected_checkpoints(conn, upgraded=False)
    runner.upgrade(_REVISION)
    with engine.connect() as conn:
        assert conn.execute(sa.text(f"SELECT {_COLUMNS} FROM egress_cursor ORDER BY id")).all() == original
        _assert_selected_checkpoints(conn, upgraded=True)
        assert conn.execute(sa.text("SELECT id, append_order FROM egress_cursor ORDER BY id")).all() == [
            (10, 0),
            (20, 0),
            (30, 0),
        ]
