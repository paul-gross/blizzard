"""Runner-store decisions that had only a comment defending them (issue #270).

Each test below pins one store-level decision whose reversion no other test caught."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner import runtime as runner_runtime
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from blizzard.runner.store.repository import NewLease
from blizzard.runner.store.schema import park_facts, pause_parks
from tests.runner_fakes import make_store

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


@pytest.mark.unit
def test_a_rebind_after_a_release_reads_as_held(tmp_path):  # type: ignore[no-untyped-def]
    """Bind -> release -> bind again on the same ``(chunk, env)``: the second binding
    is held, not read as still-released by a naive set-difference predicate."""
    store = _store(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_release(chunk_id="ch_1", environment_id="e1", released_at=_NOW + timedelta(minutes=1))
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW + timedelta(minutes=2))

    assert store.held_environment_ids() == ["e1"]
    assert store.live_tenure_chunk_ids() == ["ch_1"]
    assert [b.environment_id for b in store.bindings_for_chunk("ch_1")] == ["e1"]


@pytest.mark.unit
def test_a_same_instant_escalation_closure_does_not_mask_its_escalation(tmp_path):  # type: ignore[no-untyped-def]
    """``>``, not ``>=``: a mark stamped at the escalation's own instant must not close it
    — the sweep always stamps a later tick, and a tie would mask a fresh escalation."""
    store = _store(tmp_path)
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="escalated", closed_at=_NOW)
    store.record_escalation_closure(chunk_id="ch_1", reason="stopped", at=_NOW)  # the same instant

    assert [e.lease_id for e in store.open_escalations()] == ["lease_1"]

    store.record_escalation_closure(chunk_id="ch_1", reason="stopped", at=_NOW + timedelta(seconds=1))

    assert store.open_escalations() == []


@pytest.mark.unit
def test_a_same_instant_mint_consumes_its_requeue_mark(tmp_path):  # type: ignore[no-untyped-def]
    """``>=``, not ``>``: a spawn that stamps its lease at the requeue mark's own
    instant still consumes it — a ``>`` comparison would leave it pending forever."""
    store = _store(tmp_path)
    store.record_requeue(chunk_id="ch_1", at=_NOW)
    assert store.pending_requeue_chunk_ids() == {"ch_1"}

    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,  # the same instant the mark carries
        )
    )

    assert store.pending_requeue_chunk_ids() == set()


@pytest.mark.unit
def test_pause_parks_are_their_own_table_and_park_facts_keeps_a_non_null_question_id() -> None:
    """The pause-park split, structurally: ``park_facts.question_id`` stays NOT NULL
    and ``pause_parks`` carries no ``question_id`` at all — one NULL in the
    ``unforwarded_ask`` subquery would break ask-and-exit fleet-wide."""
    assert park_facts.c.question_id.nullable is False
    assert "question_id" not in pause_parks.c


@pytest.mark.unit
def test_set_route_token_keeps_one_current_row_per_chunk(tmp_path):  # type: ignore[no-untyped-def]
    """A re-claim of the same chunk overwrites the stashed plaintext rather than appending:
    the runner only ever presents its *current* token, so there is exactly one row to read
    and no rotation history to disambiguate."""
    store = _store(tmp_path)
    store.set_route_token("ch_1", token="rtok-old", at=_NOW)
    store.set_route_token("ch_1", token="rtok-new", at=_NOW + timedelta(minutes=1))

    assert store.route_token("ch_1") == "rtok-new"


@pytest.mark.component
def test_declaration_environment_id_migration_discards_the_pre_revision_rows(tmp_path: Path) -> None:
    """``20260726_1000`` drops and recreates ``git_commit_declarations`` — no
    back-fill; a row seeded at the parent revision must not survive the upgrade."""
    parent = "20260725_1200_runner_check_results"
    config = runner_runtime.init_environment(tmp_path)
    migrations = runner_runtime.migration_runner(config)
    migrations.downgrade(parent)

    engine = create_engine_from_url(config.db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO git_commit_declarations"
                    ' (lease_id, chunk_id, node_id, epoch, forge, repo, branch, "commit", declared_at)'
                    " VALUES ('lease_1', 'ch_1', 'nd_build', 1, 'github', 'r', 'b', 'abc', '2026-07-01 00:00:00')"
                )
            )

        migrations.upgrade("head")

        with engine.connect() as conn:
            surviving = conn.execute(sa.text("SELECT count(*) FROM git_commit_declarations")).scalar_one()
        columns = {c["name"] for c in sa.inspect(engine).get_columns("git_commit_declarations")}
    finally:
        engine.dispose()

    assert surviving == 0
    assert "environment_id" in columns
    assert "forge" not in columns


@pytest.mark.component
def test_a_migrated_transcript_outbound_seq_is_never_reissued_after_a_prune(tmp_path: Path) -> None:
    """``sqlite_autoincrement`` is declared twice, and only ``schema.py``'s copy is on the
    path a test store takes. Production migrates (`bzh:gating-tier-pins-production-paths`),
    so the revision's own copy is pinned here: dropping it reissues a pruned seq."""
    config = runner_runtime.init_environment(tmp_path)
    engine = create_engine_from_url(config.db_url)
    try:
        store = SqlAlchemyRunnerStore(engine)
        store.record_lease(
            NewLease(
                lease_id="lease_1",
                chunk_id="ch_1",
                graph_id="gr_1",
                node_id="nd_build",
                node_name="build",
                epoch=1,
                runner_id="r1",
                retries_max=2,
                created_at=_NOW,
            )
        )
        store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)
        segment_id = store.open_transcript_segments()[0].segment_id
        first = _delta(store, segment_id, cursor="tok-1")
        store.ack_transcript_outbound(first, acked_at=_NOW)  # prunes the table's only, and highest, row
        second = _delta(store, segment_id, cursor="tok-2")
    finally:
        engine.dispose()

    assert second != first


def _delta(store: SqlAlchemyRunnerStore, segment_id: str, *, cursor: str) -> int:
    (seq,) = store.record_transcript_deltas(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor=cursor,
        shipped_bytes=1,
        shipped_turns=1,
        normalizer_version="v1",
        harness_version=None,
        payloads=["{}"],
        created_at=_NOW,
    )
    return seq
