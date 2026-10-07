"""The lease tables acquire later columns and indexes only at their own revisions."""

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner.store import MIGRATIONS_DIR, schema

pytestmark = pytest.mark.component


def test_fresh_lease_tables_keep_their_historical_shapes_through_head(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    migration = MigrationRunner(script_location=MIGRATIONS_DIR, url=url)
    engine = create_engine_from_url(url)

    def shape(table: str) -> tuple[set[str], set[str]]:
        inspector = sa.inspect(engine)
        return (
            {str(column["name"]) for column in inspector.get_columns(table)},
            {str(index["name"]) for index in inspector.get_indexes(table)},
        )

    try:
        migration.upgrade("20260713_1245_runner_lease_lifecycle")
        closures = {"id", "lease_id", "chunk_id", "node_id", "reason", "closed_at"}
        assert shape("lease_closures") == (closures, set())

        migration.upgrade("20260719_0900_runner_lease_tokens")
        tokens = {"lease_id", "token_hash", "minted_at"}
        assert shape("lease_tokens") == (tokens, set())

        migration.upgrade("20261002_1100_runner_trace_cursor")
        assert shape("lease_closures") == (closures, {"ix_lease_closures_closed_at_lease_id"})

        migration.upgrade("20261002_1300_runner_lease_tokens_token_hash_index")
        assert shape("lease_tokens") == (tokens, {"ix_lease_tokens_token_hash"})

        migration.upgrade("head")
        assert shape("lease_closures") == (
            set(schema.lease_closures.c.keys()),
            {index.name for index in schema.lease_closures.indexes},
        )
        assert shape("lease_tokens") == (
            set(schema.lease_tokens.c.keys()),
            {index.name for index in schema.lease_tokens.indexes},
        )
    finally:
        engine.dispose()
