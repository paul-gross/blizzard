"""``LeaseLivenessStore``'s open-provisional-generation predicate.

``lease_spawns.session_id IS NULL`` alone can't tell a genuine two-phase provisional row
apart from a row predating that design: its columns were all added by one additive
migration (``20260916_1000_two_phase_spawn_ownership``) with no backfill, so every row
written before it reads ``session_id IS NULL`` too, for an unrelated reason."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import insert, select

from blizzard.runner.domain.leases import NewLease
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.store import schema as runner
from tests.runner_fakes import make_store

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 14, tzinfo=UTC)


def _seed_lease(store, *, lease_id: str = "lease_a", chunk_id: str = "ch_1") -> None:
    store.record_lease(
        NewLease(
            lease_id=lease_id,
            chunk_id=chunk_id,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r",
            retries_max=2,
            created_at=_NOW,
        )
    )


def _seed_legacy_row(store, *, lease_id: str = "lease_a") -> None:
    """Exactly what a pre-``20260916_1000_two_phase_spawn_ownership`` row reads as after
    that additive migration ran: every two-phase column NULL, never backfilled."""
    with store._engine.begin() as conn:
        conn.execute(insert(runner.lease_spawns).values(lease_id=lease_id, spawned_at=_NOW))


def test_record_identified_spawn_refuses_a_lease_with_only_a_legacy_row(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A legacy row is never a genuine open provisional generation — misreading it as one
    would let identification land on a row this plan's own phase-one write never created."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store)
    _seed_legacy_row(store)

    with pytest.raises(ValueError, match="no open provisional spawn generation"):
        store.record_identified_spawn("lease_a", session=SessionReference("claude_code", "sess-1"), identified_at=_NOW)


def test_record_identity_failed_is_a_silent_no_op_for_a_lease_with_only_a_legacy_row(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """``record_identity_failed``'s own ``required=False`` lookup must stay silent here too
    — a legacy row is not a provisional generation to close, one way or the other."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store)
    _seed_legacy_row(store)

    store.record_identity_failed("lease_a", at=_NOW)  # must not raise

    with store._engine.connect() as conn:
        row = conn.execute(
            select(runner.lease_spawns.c.identity_failed_at).where(runner.lease_spawns.c.lease_id == "lease_a")
        ).one()
    assert row.identity_failed_at is None  # the legacy row, untouched


def test_record_identified_spawn_finds_the_genuine_row_beside_a_legacy_one(tmp_path) -> None:  # type: ignore[no-untyped-def]
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

    with store._engine.connect() as conn:
        rows = conn.execute(
            select(runner.lease_spawns.c.pid, runner.lease_spawns.c.session_id)
            .where(runner.lease_spawns.c.lease_id == "lease_a")
            .order_by(runner.lease_spawns.c.id)
        ).all()
    assert rows[0] == (None, None)  # the legacy row, untouched
    assert rows[1] == (4242, "ses_123")  # the genuine provisional row, now identified
