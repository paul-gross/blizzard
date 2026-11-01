"""Tests proving the ``20260721_1600_hub_event_log`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine, insert, inspect

from blizzard.hub.store import schema as s
from tests.migration_support_event_log import T0
from tests.support import (
    hub_store_connections,
    migrate_to,
    stamp,
)

pytestmark = [pytest.mark.unit]

_HEAD_BEFORE_EVENT_LOG = "20260721_1500_hub_cli_auth_state_user"


def _has_table(engine: Engine, name: str) -> bool:
    return name in inspect(engine).get_table_names()


def test_migration_creates_event_log_on_in_place_upgrade(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _HEAD_BEFORE_EVENT_LOG)
    assert not _has_table(engine, "event_log")  # absent before the revision
    runner.upgrade("head")
    # After the in-place upgrade the table exists and is insertable.
    with engine.begin() as conn:
        conn.execute(
            insert(s.event_log).values(
                stamp(
                    s.event_log,
                    severity="info",
                    kind="k",
                    runner_id="r",
                    chunk_id=None,
                    lease_id=None,
                    node_name=None,
                    message="m",
                    detail=None,
                    recorded_at=T0,
                )
            )
        )
        rows = conn.execute(hub_store_connections(engine).select(s.event_log)).all()
    assert len(rows) == 1
