"""Tests proving the ``20260728_1410_chunk_defaults`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.store import MIGRATIONS_DIR
from tests.migration_support_pin_hub_api import (
    MIGRATION_CLOCK,
    scratch_store,
)
from tests.support import hub_scoped_engine

pytestmark = [pytest.mark.unit]

_GRAPH_SESSIONS = "20260728_1400_hub_graph_sessions"

_CHUNK_DEFAULTS = "20260728_1410_hub_chunk_defaults"


def test_chunk_defaults_retains_model_and_backfills_no_default_model(tmp_path: Path) -> None:
    """``chunks.model`` is kept (it is the only record of what a pre-#144 chunk ran) and
    ``default_model`` is left NULL rather than filled in from it."""
    _runner, url = scratch_store(tmp_path, _GRAPH_SESSIONS)
    engine = hub_scoped_engine(create_engine_from_url(url))
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO chunks (chunk_id, graph_id, minted_at, model)"
                    " VALUES ('ch_old', 'gr_1', '2026-01-01 00:00:00', 'claude-opus-4-8')"
                )
            )
    finally:
        engine.dispose()

    MigrationRunner(script_location=MIGRATIONS_DIR, url=url, clock=MIGRATION_CLOCK).upgrade(_CHUNK_DEFAULTS)

    engine = hub_scoped_engine(create_engine_from_url(url))
    try:
        with engine.connect() as conn:
            row = conn.execute(sa.text("SELECT model, default_model FROM chunks WHERE chunk_id = 'ch_old'")).one()
    finally:
        engine.dispose()
    assert row.model == "claude-opus-4-8"  # retained, not dropped
    assert row.default_model is None  # not backfilled from `model`
