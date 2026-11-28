"""Disposable stopped-host stores for offline invariant checks."""

from pathlib import Path

import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.store import schema
from tests.support.config_inputs import init_host, load_host_config_for_store
from tests.support.migrations.invariant_checker import NOW, insert
from tests.support.runner_fakes import FIXTURE_RUNNER_ID


def healthy(root: Path) -> None:
    """Initialize and migrate a new host, with a registered fixture runner."""
    engine = create_engine_from_url(init_host(root).db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.insert(schema.runner_identity).values(
                    runner_id=FIXTURE_RUNNER_ID, runner_name="r", registered_at=NOW
                )
            )
    finally:
        engine.dispose()


def corrupt(root: Path) -> None:
    """Add two held bindings for the same workspace environment."""
    engine = create_engine_from_url(load_host_config_for_store(root).db_url)
    try:
        with engine.begin() as conn:
            for chunk_id in ("ch_1", "ch_2"):
                conn.execute(
                    insert(schema.env_bindings).values(
                        chunk_id=chunk_id,
                        environment_id="env_shared",
                        workspace_id="ws_a",
                        workdir="/ws_a/env_shared",
                        bound_at=NOW,
                    )
                )
    finally:
        engine.dispose()
