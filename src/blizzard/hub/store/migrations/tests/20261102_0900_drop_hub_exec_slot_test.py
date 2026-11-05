"""The slot table's drop removes its rows, and the downgrade leaves the table as it stood, empty."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from tests.support.scaffolding import migrate_to

pytestmark = pytest.mark.component

_PARENT = "20261101_0900_turn_page"
_REVISION = "20261102_0900_drop_hub_exec_slot"
_TABLE = "hub_exec_slot"
_INDEX = "ix_hub_exec_slot_tenant_id"


def test_upgrade_downgrade_and_reupgrade(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        conn.execute(
            sa.text(
                "INSERT INTO hub_exec_slot (slot_id, holder_chunk_id, node_id, acquired_at, tenant_id) "
                "VALUES ('hes_1', 'ch_1', 'nd_1', '2026-10-30', 'tenant_1')"
            )
        )

    def present() -> bool:
        return sa.inspect(engine).has_table(_TABLE)

    assert present()
    runner.upgrade(_REVISION)
    assert not present()
    runner.downgrade(_PARENT)
    inspector = sa.inspect(engine)
    assert present()
    assert [c["name"] for c in inspector.get_columns(_TABLE)] == [
        "slot_id",
        "holder_chunk_id",
        "node_id",
        "acquired_at",
        "released_at",
        "tenant_id",
    ]
    assert {i["name"]: i["column_names"] for i in inspector.get_indexes(_TABLE)} == {_INDEX: ["tenant_id"]}
    assert {(fk["referred_table"], fk["name"]) for fk in inspector.get_foreign_keys(_TABLE)} == {
        ("chunks", None),
        ("tenants", "fk_hub_exec_slot_tenant_id"),
    }
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM hub_exec_slot")).scalar() == 0
    runner.upgrade(_REVISION)
    assert not present()
