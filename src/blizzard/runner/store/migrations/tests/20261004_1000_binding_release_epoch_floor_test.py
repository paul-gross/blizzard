"""Tests proving the ``20261004_1000_binding_release_epoch_floor`` revision."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.utc import UtcDateTime
from blizzard.runner import runtime as runner_runtime
from tests.migration_support_runner_store_migrations import (
    _RUNNER,
    _leases,
    _seed_identity_before_the_scope,
)
from tests.runner_fakes import SqlAlchemyRunnerStore, runner_store_errors

pytestmark = [pytest.mark.component]

# ``lease_closures`` and ``binding_releases`` as the release-floor revision's parent held them.
_lease_closures_at_floor = sa.table(
    "lease_closures",
    sa.column("lease_id"),
    sa.column("chunk_id"),
    sa.column("node_id"),
    sa.column("reason"),
    sa.column("closed_at", UtcDateTime()),
)

_binding_releases_at_floor = sa.table(
    "binding_releases", sa.column("chunk_id"), sa.column("environment_id"), sa.column("released_at", UtcDateTime())
)


def test_release_floor_backfill_does_not_include_a_later_equal_instant_mint(tmp_path: Path) -> None:
    config = runner_runtime.init_environment(tmp_path)
    migration = runner_runtime.migration_runner(config)
    migration.downgrade("20261003_1000_runner_transcript_segment_spawn_cwd")
    engine = create_engine_from_url(config.db_url)
    instant = datetime(2026, 7, 13, 12, tzinfo=UTC)
    try:
        with engine.begin() as conn:
            for lease_id, epoch in (("old", 1), ("new", 3)):
                conn.execute(
                    _leases.insert().values(
                        lease_id=lease_id, chunk_id="ch_1", epoch=epoch, runner_id="r1", created_at=instant
                    )
                )
            conn.execute(
                _lease_closures_at_floor.insert().values(
                    lease_id="old", chunk_id="ch_1", node_id="nd_build", reason="released", closed_at=instant
                )
            )
            conn.execute(
                _binding_releases_at_floor.insert().values(chunk_id="ch_1", environment_id="e1", released_at=instant)
            )
        _seed_identity_before_the_scope(migration, engine)
        migration.upgrade("head")
        with engine.connect() as conn:
            floor = conn.execute(
                sa.select(sa.column("lease_epoch_floor")).select_from(sa.table("binding_releases"))
            ).scalar_one()
        store = SqlAlchemyRunnerStore(engine, runner_store_errors(), _RUNNER)
        assert floor == 1
        assert store.has_lease_in_binding_tenure("ch_1", instant)
    finally:
        engine.dispose()
