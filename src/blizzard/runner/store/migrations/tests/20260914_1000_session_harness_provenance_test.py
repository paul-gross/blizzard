"""Tests proving the ``20260914_1000_session_harness_provenance`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.store import MIGRATIONS_DIR
from tests.support import upgrade_runner_store_as_registered

pytestmark = [pytest.mark.component]

_PARENT = "20260913_1100_runner_store_indexes"

_STAMP = "2026-09-11 12:00:00"


def test_session_owner_migration_stamps_old_runner_observations_without_inventing_versions(tmp_path: Path) -> None:
    """Every historic session fact gets its frozen owner; invocation versions stay unknown."""
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    migrations = MigrationRunner(script_location=MIGRATIONS_DIR, url=url)
    migrations.upgrade(_PARENT)
    engine = create_engine_from_url(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO leases (lease_id, chunk_id, epoch, runner_id, session_id, created_at) "
                    "VALUES ('lease_1', 'chunk_1', 1, 'runner_1', 'shared', :at)"
                ),
                {"at": _STAMP},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO lease_context (lease_id, chunk_id, graph_id, node_id, node_name, retries_max, recorded_at) "
                    "VALUES ('lease_1', 'chunk_1', 'graph_1', 'node_1', 'build', 1, :at)"
                ),
                {"at": _STAMP},
            )
            conn.execute(
                sa.text("INSERT INTO lease_spawns (lease_id, spawned_at) VALUES ('lease_1', :at)"), {"at": _STAMP}
            )
            conn.execute(
                sa.text(
                    "INSERT INTO asks (lease_id, chunk_id, question_id, question, options, session_id, asked_at) "
                    "VALUES ('lease_1', 'chunk_1', 'question_1', 'why?', '[]', 'shared', :at)"
                ),
                {"at": _STAMP},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO takeovers (takeover_id, chunk_id, lease_id, session_id, workdir, opened_at) "
                    "VALUES ('takeover_1', 'chunk_1', 'lease_1', 'shared', '/work', :at)"
                ),
                {"at": _STAMP},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO session_preamble_facts (session_id, blizzard_digest, workspace_digest, recorded_at) "
                    "VALUES ('shared', 'b', 'w', :at)"
                ),
                {"at": _STAMP},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO context_samples (lease_id, session_id, context_tokens, sampled_at) "
                    "VALUES ('lease_1', 'shared', 42, :at)"
                ),
                {"at": _STAMP},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO transcript_segments "
                    "(segment_id, chunk_id, node_id, epoch, generation, lease_id, session_id, cursor, "
                    "shipped_bytes, shipped_turns, normalizer_version, stamped_at) "
                    "VALUES ('segment_1', 'chunk_1', 'node_1', 1, 1, 'lease_1', 'shared', NULL, 0, 0, '', :at)"
                ),
                {"at": _STAMP},
            )
    finally:
        engine.dispose()

    upgrade_runner_store_as_registered(migrations, url)
    engine = create_engine_from_url(url)
    try:
        with engine.connect() as conn:
            for table in (
                "leases",
                "asks",
                "takeovers",
                "session_preamble_facts",
                "context_samples",
                "transcript_segments",
            ):
                assert conn.execute(sa.text(f"SELECT harness_id FROM {table}")).scalar_one() == CLAUDE_CODE_HARNESS_ID
            generation = conn.execute(sa.text("SELECT harness_id, harness_version FROM lease_spawns")).one()
            assert tuple(generation) == (CLAUDE_CODE_HARNESS_ID, None)
            # The migration changes provenance only; the previously observed cursor/counters stay facts.
            assert conn.execute(
                sa.text("SELECT cursor, shipped_bytes, shipped_turns FROM transcript_segments")
            ).one() == (None, 0, 0)
    finally:
        engine.dispose()
