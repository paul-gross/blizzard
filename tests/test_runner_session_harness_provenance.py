"""Runner session-owner migration and composite store behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.harness.fingerprint import PreambleFingerprint
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.store import MIGRATIONS_DIR
from tests.runner_fakes import make_store

pytestmark = pytest.mark.component

_PARENT = "20260913_1100_runner_store_indexes"
_STAMP = "2026-09-11 12:00:00"
_NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


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

    migrations.upgrade("head")
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


@pytest.mark.unit
def test_session_reference_is_a_composite_key() -> None:
    """A same raw id remains distinguishable in every domain value that carries it."""
    claude = SessionReference(CLAUDE_CODE_HARNESS_ID, "shared")
    other = SessionReference("other_harness", "shared")
    assert claude != other
    assert {claude, other} == {claude, other}


def test_equal_raw_session_ids_are_isolated_across_runner_session_repositories(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'composite.db'}")
    claude = SessionReference(CLAUDE_CODE_HARNESS_ID, "shared")
    other = SessionReference("other_harness", "shared")

    for ordinal, (lease_id, session) in enumerate((("lease_a", claude), ("lease_b", other)), start=1):
        at = _NOW + timedelta(minutes=ordinal)
        store.record_lease(
            NewLease(
                lease_id=lease_id,
                chunk_id="chunk_1",
                graph_id="graph_1",
                node_id="node_1",
                node_name="build",
                epoch=ordinal,
                runner_id="runner_1",
                retries_max=1,
                created_at=at,
                session_name="shared-pool",
            )
        )
        store.record_spawn(
            lease_id,
            pid=ordinal,
            process_start_time=str(ordinal),
            session=session,
            harness_version=f"v{ordinal}",
            spawned_at=at,
        )
        store.record_usage(
            lease_id=lease_id,
            chunk_id="chunk_1",
            node_id="node_1",
            epoch=ordinal,
            generation=1,
            sample=UsageSample("spawn", "model", ordinal, 0, 0, 0, None),
            recorded_at=at,
        )
        store.record_context_sample(
            lease_id=lease_id,
            chunk_id="chunk_1",
            session=session,
            context_tokens=ordinal * 100,
            sampled_at=at,
        )
        store.record_session_preamble(
            session,
            fingerprint=PreambleFingerprint(blizzard=f"b{ordinal}", workspace=f"w{ordinal}"),
            at=at,
        )
        store.record_ask(
            lease_id=lease_id,
            chunk_id="chunk_1",
            question_id=f"question_{ordinal}",
            question="question",
            options=[],
            session=session,
            asked_at=at,
        )
        store.record_takeover(
            takeover_id=f"takeover_{ordinal}",
            chunk_id=f"chunk_{ordinal}",
            lease_id=lease_id,
            session=session,
            workdir="/work",
            fence_epoch=None,
            opened_at=at,
        )

    lease_a = store.lease_for_session(claude)
    lease_b = store.lease_for_session(other)
    assert lease_a is not None and lease_a.lease_id == "lease_a"
    assert lease_b is not None and lease_b.lease_id == "lease_b"
    assert store.session_invocation_count(claude) == 1
    assert store.session_invocation_count(other) == 1
    assert store.session_preamble_fingerprint(claude) == PreambleFingerprint(blizzard="b1", workspace="w1")
    assert store.session_preamble_fingerprint(other) == PreambleFingerprint(blizzard="b2", workspace="w2")
    state_a = store.context_sample_state("lease_a")
    state_b = store.context_sample_state("lease_b")
    assert state_a is not None and state_a.max_context_tokens == 100
    assert state_b is not None and state_b.max_context_tokens == 200
    assert {ask.session for ask in store.open_asks()} == {claude, other}
    assert {takeover.session for takeover in store.open_takeovers()} == {claude, other}

    segments = store.transcript_segments_for_chunk("chunk_1")
    assert {segment.session for segment in segments} == {claude, other}
    assert all(segment.cursor is None for segment in segments)
    pool_head = store.pool_head("chunk_1", "shared-pool")
    assert pool_head is not None and pool_head.session == other

    with store._engine.connect() as conn:
        generations = conn.execute(sa.text("SELECT lease_id, harness_id, harness_version FROM lease_spawns"))
        assert {tuple(row) for row in generations} == {
            ("lease_a", CLAUDE_CODE_HARNESS_ID, "v1"),
            ("lease_b", "other_harness", "v2"),
        }
