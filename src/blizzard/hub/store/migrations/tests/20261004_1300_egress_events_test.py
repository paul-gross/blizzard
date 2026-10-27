"""Tests proving the ``20261004_1300_egress_events`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime
from tests.config_inputs import init_hub

pytestmark = [pytest.mark.unit]

_DERIVATIONS_EGRESS_ORDER = {
    "ix_transcript_event_derivations_derived_at_segment_id",
    "ix_transcript_event_derivations_egress_order",
}

_DROPS_BY_CHUNK = {"ix_transcript_event_drops_chunk_id_epoch", "ix_transcript_event_drops_tenant_chunk_id_epoch"}


def test_egress_events_indexes_and_cursor_columns_survive_migration_roundtrip(tmp_path: Path) -> None:
    """Hub-only — each index and cursor column, downgraded by id below the revision."""
    config = init_hub(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)

    def _shape() -> tuple[bool, bool, bool, set[str]]:
        engine = create_engine_from_url(config.db_url)
        try:
            inspector = sa.inspect(engine)

            def indexes(table: str) -> set[str]:
                return {str(i["name"]) for i in inspector.get_indexes(table)}

            columns = {c["name"] for c in inspector.get_columns("egress_cursor")}
            return (
                bool(indexes("transcript_event_derivations") & _DERIVATIONS_EGRESS_ORDER),
                "ix_lease_facts_minted_at" in indexes("lease_facts"),
                bool(indexes("transcript_event_drops") & _DROPS_BY_CHUNK),
                columns & {"segment_id", "extractor_version"},
            )
        finally:
            engine.dispose()

    assert _shape() == (True, True, True, {"segment_id", "extractor_version"})

    runner.downgrade("20261004_1000_hub_secrets")
    assert _shape() == (False, False, False, set())

    runner.upgrade("head")
    assert _shape() == (True, True, True, {"segment_id", "extractor_version"})
