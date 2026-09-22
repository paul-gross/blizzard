"""The runner-side provider-overload backoff store (blizzard#595).

``record_overload`` is check-then-insert, mirroring ``nudge_facts``; ``open_overload_facts``
must read only the newest, un-reset fact per (lease, epoch) — a superseded or fallen-through
one is not a backing-off candidate. Driven directly against a real (tmp sqlite) store, no
loop context."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.runner_fakes import make_store

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _record(
    store,  # type: ignore[no-untyped-def]
    *,
    lease_id: str = "lease_1",
    chunk_id: str = "ch_1",
    epoch: int = 1,
    generation: int = 1,
    invocation_kind: str = "worker",
    invocation_identity: str = "1",
    streak_ordinal: int = 1,
    observed_at: datetime = _NOW,
    resume_after: datetime | None = None,
) -> None:
    store.record_overload(
        lease_id=lease_id,
        chunk_id=chunk_id,
        epoch=epoch,
        generation=generation,
        invocation_kind=invocation_kind,
        invocation_identity=invocation_identity,
        streak_ordinal=streak_ordinal,
        observed_at=observed_at,
        resume_after=resume_after,
    )


def test_record_overload_is_idempotent_for_the_same_invocation(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Re-classifying the same exit on a later pass must write nothing — keyed on
    (lease_id, epoch, invocation_kind, invocation_identity)."""
    store = _store(tmp_path)
    _record(store, resume_after=_NOW + timedelta(seconds=60))
    _record(store, resume_after=_NOW + timedelta(seconds=999))  # same identity — ignored

    facts = store.open_overload_facts()
    assert len(facts) == 1
    assert facts[0].resume_after == _NOW + timedelta(seconds=60)


def test_a_different_invocation_identity_records_its_own_fact(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _record(store, invocation_identity="1", generation=1, resume_after=_NOW + timedelta(seconds=60))
    _record(
        store,
        invocation_identity="2",
        generation=2,
        streak_ordinal=2,
        observed_at=_NOW + timedelta(seconds=60),
        resume_after=_NOW + timedelta(seconds=180),
    )

    assert store.overload_streak("lease_1", 1) == 2


def test_open_overload_facts_excludes_a_fallen_through_fact(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A streak-limit fall-through's own fact carries no ``resume_after`` — never a
    backing-off candidate."""
    store = _store(tmp_path)
    _record(store, streak_ordinal=5, resume_after=None)

    assert store.open_overload_facts() == []


def test_a_later_overload_supersedes_an_earlier_one_on_the_same_lease_epoch(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _record(
        store,
        invocation_identity="1",
        generation=1,
        observed_at=_NOW,
        resume_after=_NOW + timedelta(seconds=60),
    )
    _record(
        store,
        invocation_identity="2",
        generation=2,
        streak_ordinal=2,
        observed_at=_NOW + timedelta(seconds=60),
        resume_after=_NOW + timedelta(seconds=180),
    )

    facts = store.open_overload_facts()
    assert len(facts) == 1
    assert facts[0].invocation_identity == "2"
    assert facts[0].streak_ordinal == 2


def test_record_reset_closes_every_open_fact_on_the_lease_epoch(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _record(store, resume_after=_NOW + timedelta(seconds=60))

    store.record_reset(lease_id="lease_1", epoch=1, at=_NOW + timedelta(seconds=90))

    assert store.open_overload_facts() == []
    assert store.overload_streak("lease_1", 1) == 0


def test_overload_streak_counts_only_since_the_latest_reset(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A fresh overload after a clean-exit reset starts a new streak at ordinal 1, not a
    continuation of the one the reset closed."""
    store = _store(tmp_path)
    _record(store, invocation_identity="1", generation=1, observed_at=_NOW, resume_after=_NOW + timedelta(seconds=60))
    store.record_reset(lease_id="lease_1", epoch=1, at=_NOW + timedelta(seconds=90))

    assert store.overload_streak("lease_1", 1) == 0

    _record(
        store,
        invocation_identity="3",
        generation=3,
        streak_ordinal=1,
        observed_at=_NOW + timedelta(seconds=200),
        resume_after=_NOW + timedelta(seconds=260),
    )
    assert store.overload_streak("lease_1", 1) == 1


def test_overload_streak_is_scoped_per_epoch(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A restart under a fresh epoch owes the prior epoch's own streak nothing — a new
    epoch always starts clean."""
    store = _store(tmp_path)
    _record(store, epoch=1, resume_after=_NOW + timedelta(seconds=60))

    assert store.overload_streak("lease_1", 2) == 0
