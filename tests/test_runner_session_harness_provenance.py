"""Runner session-owner migration and composite store behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.runner.harness.fingerprint import PreambleFingerprint
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.leases.model import NewLease
from tests.runner_fakes import make_store, record_usage

pytestmark = [pytest.mark.component]

_NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


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
        record_usage(
            store,
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


def test_transcript_segment_freezes_the_leases_own_resolved_model_and_effort(tmp_path: Path) -> None:
    """The pair is read off `lease_context` at segment open, exactly as
    `harness_id` already is — not re-resolved at send time."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    store.record_lease(
        NewLease(
            lease_id="lease_a",
            chunk_id="chunk_1",
            graph_id="graph_1",
            node_id="node_1",
            node_name="build",
            epoch=1,
            retries_max=1,
            created_at=_NOW,
            resolved_model="claude-sonnet-5",
            resolved_effort="high",
        )
    )

    store.record_spawn(
        "lease_a",
        pid=1,
        process_start_time="1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
        spawn_cwd="/ws",
    )

    (segment,) = store.transcript_segments_for_chunk("chunk_1")
    assert segment.model == "claude-sonnet-5"
    assert segment.effort == "high"
    assert segment.spawn_cwd == "/ws"


def test_transcript_segment_leaves_model_and_effort_unset_when_unresolved(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    store.record_lease(
        NewLease(
            lease_id="lease_a",
            chunk_id="chunk_1",
            graph_id="graph_1",
            node_id="node_1",
            node_name="build",
            epoch=1,
            retries_max=1,
            created_at=_NOW,
        )
    )

    store.record_spawn(
        "lease_a",
        pid=1,
        process_start_time="1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )

    (segment,) = store.transcript_segments_for_chunk("chunk_1")
    assert segment.model is None
    assert segment.effort is None
    assert segment.spawn_cwd is None


def test_identified_spawn_freezes_the_spawn_cwd_onto_its_segment(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    store.record_lease(
        NewLease(
            lease_id="lease_a",
            chunk_id="chunk_1",
            graph_id="graph_1",
            node_id="node_1",
            node_name="build",
            epoch=1,
            retries_max=1,
            created_at=_NOW,
        )
    )
    store.record_provisional_spawn(
        "lease_a", pid=1, process_start_time="1", pgid=None, spawned_at=_NOW, harness_id=CLAUDE_CODE_HARNESS_ID
    )

    store.record_identified_spawn(
        "lease_a",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        identified_at=_NOW,
        spawn_cwd="/ws",
    )

    (segment,) = store.transcript_segments_for_chunk("chunk_1")
    assert segment.spawn_cwd == "/ws"
