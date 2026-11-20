"""``LeaseLivenessStore``'s open-provisional-generation predicate.

``lease_spawns.session_id IS NULL`` alone can't tell a genuine two-phase provisional row
apart from a row predating that design: its columns were all added by one additive
migration (``20260916_1000_two_phase_spawn_ownership``) with no backfill, so every row
written before it reads ``session_id IS NULL`` too, for an unrelated reason."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert, select

from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.observation import ExecutionActivity, ExecutionLiveness, ExecutionObservation
from blizzard.runner.leases.model import NewLease
from blizzard.runner.store import schema as runner
from tests.support.runner_fakes import FIXTURE_RUNNER_ID, SqlAlchemyRunnerStore, make_store
from tests.support.runner_mint import new_mint
from tests.support.scaffolding import count_rows_read

_NOW = datetime(2026, 7, 14, tzinfo=UTC)


def _seed_lease(store: SqlAlchemyRunnerStore, *, lease_id: str = "lease_a", chunk_id: str = "ch_1") -> None:
    store.record_mint(
        new_mint(
            NewLease(
                lease_id=lease_id,
                chunk_id=chunk_id,
                graph_id="gr_1",
                node_id="nd_build",
                node_name="build",
                epoch=1,
                retries_max=2,
                created_at=_NOW,
            )
        )
    )


def _seed_legacy_row(store: SqlAlchemyRunnerStore, *, lease_id: str = "lease_a") -> None:
    """Exactly what a pre-``20260916_1000_two_phase_spawn_ownership`` row reads as after
    that additive migration ran: every two-phase column NULL, never backfilled."""
    with store.engine.begin() as conn:
        conn.execute(
            insert(runner.lease_spawns).values(runner_id=FIXTURE_RUNNER_ID, lease_id=lease_id, spawned_at=_NOW)
        )


def test_record_identified_spawn_refuses_a_lease_with_only_a_legacy_row(tmp_path: Path) -> None:
    """A legacy row (every two-phase column NULL) is not an open provisional generation, so
    identification refuses rather than landing on it."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store)
    _seed_legacy_row(store)

    with pytest.raises(ValueError, match="no open provisional spawn generation"):
        store.record_identified_spawn("lease_a", session=SessionReference("claude_code", "sess-1"), identified_at=_NOW)


def test_record_identity_failed_is_a_silent_no_op_for_a_lease_with_only_a_legacy_row(tmp_path: Path) -> None:
    """``record_identity_failed``'s own ``required=False`` lookup must stay silent here too
    — a legacy row is not a provisional generation to close, one way or the other."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store)
    _seed_legacy_row(store)

    store.record_identity_failed("lease_a", at=_NOW)  # must not raise

    with store.engine.connect() as conn:
        row = conn.execute(
            select(runner.lease_spawns.c.identity_failed_at).where(
                runner.lease_spawns.c.lease_id == "lease_a", runner.lease_spawns.c.runner_id == FIXTURE_RUNNER_ID
            )
        ).one()
    assert row.identity_failed_at is None  # the legacy row, untouched


def test_record_identified_spawn_finds_the_genuine_row_beside_a_legacy_one(tmp_path: Path) -> None:
    """The realistic combination: a lease minted before the two-phase design still active
    today, now given a real provisional spawn — the genuine row, not the legacy one
    sitting beside it, is what gets identified."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store)
    _seed_legacy_row(store)
    store.record_provisional_spawn(
        "lease_a", pid=4242, process_start_time="start-4242", pgid=4242, spawned_at=_NOW, harness_id="opencode"
    )

    store.record_identified_spawn("lease_a", session=SessionReference("opencode", "ses_123"), identified_at=_NOW)

    with store.engine.connect() as conn:
        rows = conn.execute(
            select(runner.lease_spawns.c.pid, runner.lease_spawns.c.session_id)
            .where(runner.lease_spawns.c.lease_id == "lease_a", runner.lease_spawns.c.runner_id == FIXTURE_RUNNER_ID)
            .order_by(runner.lease_spawns.c.id)
        ).all()
    assert rows[0] == (None, None)  # the legacy row, untouched
    assert rows[1] == (4242, "ses_123")  # the genuine provisional row, now identified


def test_liveness_facts_reads_one_evidence_row_per_lease_whatever_the_generation_depth(tmp_path: Path) -> None:
    """Each lease's newest qualifying generation is chosen in SQL: a deeper respawn history reads no more
    rows, and a newer generation holding only unknown evidence hides none."""

    def facts_at(depth: int, path: Path) -> tuple[int, dict[str, tuple[int, datetime]]]:
        path.mkdir()
        store = make_store(f"sqlite:///{path / 'runner.db'}")
        for lease in ("lease_a", "lease_b"):
            for generation in range(1, depth + 1):
                for minute in (0, 1):
                    at = _NOW + timedelta(minutes=10 * generation + minute)
                    alive = ExecutionObservation(
                        liveness=ExecutionLiveness.ALIVE, activity=ExecutionActivity.TOOL, evidence_at=at
                    )
                    store.record_execution_observation(
                        lease_id=lease, chunk_id="ch", generation=generation, observation=alive, observed_at=at
                    )
            store.record_execution_observation(
                lease_id=lease,
                chunk_id="ch",
                generation=depth + 1,
                observation=ExecutionObservation.unknown("not yet answering"),
                observed_at=_NOW + timedelta(hours=depth),
            )
            store.record_heartbeat(lease_id=lease, beat_at=_NOW)
        rows = count_rows_read(store.engine, lambda: store.liveness_facts(["lease_a", "lease_b"]))
        evidence = {
            lease: (facts.latest_evidence.generation, facts.latest_evidence.evidence_at)
            for lease, facts in store.liveness_facts(["lease_a", "lease_b"]).items()
            if facts.latest_evidence is not None
        }
        return rows, evidence

    shallow_rows, shallow = facts_at(2, tmp_path / "shallow")
    deep_rows, deep = facts_at(8, tmp_path / "deep")

    assert shallow_rows == deep_rows
    assert shallow == dict.fromkeys(("lease_a", "lease_b"), (2, _NOW + timedelta(minutes=21)))
    assert deep == dict.fromkeys(("lease_a", "lease_b"), (8, _NOW + timedelta(minutes=81)))
