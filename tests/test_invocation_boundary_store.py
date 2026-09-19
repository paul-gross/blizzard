"""``InvocationBoundaryStore`` — the durable transcript invocation-boundary ledger
(blizzard#437 D6/D11, unit tier)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.runner_fakes import make_store

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_record_boundary_open_is_readable_back() -> None:
    store = make_store("sqlite://")
    store.record_boundary_open(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        kind="spawn",
        start_position=None,
        opened_at=_T0,
    )

    boundary = store.boundary("lease_1", 1, "spawn")
    assert boundary is not None
    assert boundary.lease_id == "lease_1"
    assert boundary.chunk_id == "ch_1"
    assert boundary.generation == 1
    assert boundary.kind == "spawn"
    assert boundary.start_position is None
    assert boundary.opened_at == _T0
    assert boundary.closed_at is None
    assert boundary.closed_reason is None


def test_boundary_of_an_unopened_invocation_is_none() -> None:
    store = make_store("sqlite://")
    assert store.boundary("lease_1", 1, "spawn") is None


def test_record_boundary_open_is_idempotent_under_replay() -> None:
    """Check-then-insert (D7): a replayed open for an already-open ``(lease, generation,
    kind)`` writes nothing a second time — the original ``start_position`` survives."""
    store = make_store("sqlite://")
    store.record_boundary_open(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        kind="resume",
        start_position="pos-original",
        opened_at=_T0,
    )
    store.record_boundary_open(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        kind="resume",
        start_position="pos-replayed",
        opened_at=_T0,
    )

    boundary = store.boundary("lease_1", 1, "resume")
    assert boundary is not None
    assert boundary.start_position == "pos-original"


def test_distinct_kinds_at_the_same_generation_coexist() -> None:
    """A nudge and the resume it triggers share one generation number but distinct kinds
    (D5) — two rows, never a collision."""
    store = make_store("sqlite://")
    store.record_boundary_open(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=2,
        kind="nudge",
        start_position="pos-a",
        opened_at=_T0,
    )

    assert store.boundary("lease_1", 2, "nudge") is not None
    assert store.boundary("lease_1", 2, "resume") is None


def test_open_boundaries_for_lease_excludes_closed_rows() -> None:
    store = make_store("sqlite://")
    store.record_boundary_open(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        kind="spawn",
        start_position=None,
        opened_at=_T0,
    )
    store.record_boundary_open(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        kind="judge",
        start_position="pos-judge",
        opened_at=_T0,
    )

    assert {b.kind for b in store.open_boundaries_for_lease("lease_1")} == {"spawn", "judge"}

    store.close_boundaries_for_lease("lease_1", reason="failed", at=_T0)

    assert store.open_boundaries_for_lease("lease_1") == []
    closed = store.boundary("lease_1", 1, "spawn")
    assert closed is not None
    assert closed.closed_at == _T0
    assert closed.closed_reason == "failed"


def test_close_boundaries_for_lease_is_idempotent_under_replay() -> None:
    store = make_store("sqlite://")
    store.record_boundary_open(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        kind="spawn",
        start_position=None,
        opened_at=_T0,
    )
    store.close_boundaries_for_lease("lease_1", reason="failed", at=_T0)
    # A retry of the closure path (crash-and-retry) calls close again — a no-op UPDATE,
    # never a second closed_reason overwriting the first.
    store.close_boundaries_for_lease("lease_1", reason="released", at=_T0)

    closed = store.boundary("lease_1", 1, "spawn")
    assert closed is not None
    assert closed.closed_reason == "failed"


def test_close_boundaries_for_lease_leaves_other_leases_untouched() -> None:
    store = make_store("sqlite://")
    store.record_boundary_open(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        kind="spawn",
        start_position=None,
        opened_at=_T0,
    )
    store.record_boundary_open(
        lease_id="lease_2",
        chunk_id="ch_2",
        node_id="nd_build",
        epoch=1,
        generation=1,
        kind="spawn",
        start_position=None,
        opened_at=_T0,
    )

    store.close_boundaries_for_lease("lease_1", reason="failed", at=_T0)

    assert store.open_boundaries_for_lease("lease_1") == []
    assert len(store.open_boundaries_for_lease("lease_2")) == 1
