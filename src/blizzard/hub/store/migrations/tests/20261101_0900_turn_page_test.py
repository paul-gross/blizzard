"""Turn-page index is additive and reversible without changing existing turns."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from tests.support import migrate_to

pytestmark = pytest.mark.component

_PARENT = "20261030_0900_park_wait"
_REVISION = "20261101_0900_turn_page"
_INDEX = "ix_hub_turns_tenant_submission_turn"


def test_upgrade_downgrade_and_reupgrade(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        conn.execute(
            sa.text(
                "INSERT INTO hub_turns (tenant_id, turn_id, submission_id, started_at, ended_at, outcome) "
                "VALUES ('tenant_1', 'turn_1', 'sub_1', '2026-10-30', '2026-10-31', 'interrupted')"
            )
        )

    def indexes() -> dict[str | None, list[str | None]]:
        return {index["name"]: index["column_names"] for index in sa.inspect(engine).get_indexes("hub_turns")}

    assert _INDEX not in indexes()
    runner.upgrade(_REVISION)
    assert indexes()[_INDEX] == ["tenant_id", "submission_id", "turn_id"]
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT turn_id, outcome FROM hub_turns")).all() == [("turn_1", "interrupted")]
    runner.downgrade(_PARENT)
    assert _INDEX not in indexes()
    runner.upgrade(_REVISION)
    assert indexes()[_INDEX] == ["tenant_id", "submission_id", "turn_id"]
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT turn_id, outcome FROM hub_turns")).all() == [("turn_1", "interrupted")]
