"""Tests proving the ``20261003_1200_transcript_event_drops`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime
from tests.config_inputs import init_hub

pytestmark = [pytest.mark.unit]

_DROPS_EGRESS_ORDER = "ix_transcript_event_drops_egress_order"


def test_transcript_event_drops_table_survives_migration_roundtrip(tmp_path: Path) -> None:
    """Hub-only — downgrades to the revision's own parent, so the dropped table is asserted
    rather than inferred from a revision marker."""
    config = init_hub(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)

    def _shape() -> tuple[bool, bool]:
        engine = create_engine_from_url(config.db_url)
        try:
            inspector = sa.inspect(engine)
            if "transcript_event_drops" not in inspector.get_table_names():
                return False, False
            indexes = {i["name"] for i in inspector.get_indexes("transcript_event_drops")}
            # The egress twin as the revision that last built it names it.
            return True, bool(indexes & {"ix_transcript_event_drops_dropped_at_segment_id", _DROPS_EGRESS_ORDER})
        finally:
            engine.dispose()

    assert _shape() == (True, True)

    runner.downgrade("20261003_0900_egress_cursor")
    assert _shape() == (False, False)

    runner.upgrade("head")
    assert _shape() == (True, True)
