"""Tests proving the ``20260907_1000_event_log_runner_id_nullable`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.store import MIGRATIONS_DIR as HUB_MIGRATIONS_DIR
from tests.support import MIGRATION_CLOCK

pytestmark = [pytest.mark.unit]


def test_event_log_runner_id_becomes_nullable_and_downgrade_restores_the_hub_sentinel(tmp_path: Path) -> None:
    """``event_log.runner_id`` (blizzard-context:/domain/operations.md) widens to nullable
    — a hub-authored event names no runner. Reached forward from ``base``, never by
    downgrading from head (``test_frozen_table_lacks_a_later_revisions_columns``'s own
    stated reason): the pre-reshape shape is ``NOT NULL``, exactly what the frozen
    creating revision now declares."""
    url = f"sqlite:///{tmp_path / 'store.db'}"
    runner = MigrationRunner(script_location=HUB_MIGRATIONS_DIR, url=url, clock=MIGRATION_CLOCK)

    def _nullable() -> bool:
        engine = create_engine_from_url(url)
        try:
            columns = {c["name"]: c for c in sa.inspect(engine).get_columns("event_log")}
            return bool(columns["runner_id"]["nullable"])
        finally:
            engine.dispose()

    def _runner_ids() -> list[str | None]:
        engine = create_engine_from_url(url)
        try:
            with engine.connect() as conn:
                return [row[0] for row in conn.execute(sa.text("select runner_id from event_log")).all()]
        finally:
            engine.dispose()

    # The pre-reshape shape — this reshape's own parent, frozen by 20260721_1600. Forward
    # from base, never downgrade-from-head, on the same reasoning as
    # test_frozen_table_lacks_a_later_revisions_columns: the reshaping revision's own
    # downgrade() could mask a freeze that wrongly declared the column already nullable.
    runner.upgrade("20260907_0900_drop_node_mode")
    assert _nullable() is False
    engine = create_engine_from_url(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "insert into event_log (recorded_at, severity, kind, runner_id, message) "
                    "values (:recorded_at, 'info', 'work-item-closed', 'hub', 'closed')"
                ),
                {"recorded_at": "2026-08-01 12:00:00"},
            )
    finally:
        engine.dispose()

    # The upgrade both widens the column and backfills the sentinel already stored
    # there — a pre-existing hub-authored row stops naming "hub" without a rewrite.
    runner.upgrade("head")
    assert _nullable() is True
    assert _runner_ids() == [None]

    runner.downgrade("20260907_0900_drop_node_mode")
    assert _nullable() is False
    assert _runner_ids() == ["hub"]

    runner.upgrade("head")
    assert _nullable() is True
