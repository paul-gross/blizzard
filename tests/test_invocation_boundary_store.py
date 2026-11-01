"""``InvocationBoundaryStore`` — the durable transcript invocation-boundary ledger
(unit tier)."""

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
    # The default — never conflated with a genuine read failure unless requested.
    assert boundary.start_unreadable is False
    assert boundary.opened_at == _T0
    assert boundary.closed_at is None
    assert boundary.closed_reason is None


def test_record_boundary_open_persists_start_unreadable() -> None:
    """``start_unreadable=True`` is durable and distinct from ``start_position is None``'s
    fresh-session meaning."""
    store = make_store("sqlite://")
    store.record_boundary_open(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=2,
        kind="resume",
        start_position=None,
        start_unreadable=True,
        opened_at=_T0,
    )

    boundary = store.boundary("lease_1", 2, "resume")
    assert boundary is not None
    assert boundary.start_position is None
    assert boundary.start_unreadable is True


def test_boundary_of_an_unopened_invocation_is_none() -> None:
    store = make_store("sqlite://")
    assert store.boundary("lease_1", 1, "spawn") is None


def test_record_boundary_open_is_idempotent_under_replay() -> None:
    """Check-then-insert: a replayed open for an already-open ``(lease, generation,
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
    — two rows, never a collision."""
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


def _open_judge(store, *, position: str | None = "pos-original") -> None:  #
    store.record_boundary_open(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        kind="judge",
        start_position=position,
        opened_at=_T0,
    )


def test_advances_append_and_leave_the_marker_as_the_true_start() -> None:
    """A judge park-resume reuses the standing ``"judge"`` marker for its fresh elicitation;
    each advance is its own fact, so the marker keeps its original start and time while
    ``current_start`` answers the newest advance."""
    store = make_store("sqlite://")
    _open_judge(store)

    first, second = _T0.replace(hour=1), _T0.replace(hour=2)
    store.record_boundary_advance(
        lease_id="lease_1",
        generation=1,
        kind="judge",
        superseded_invocation="el-1",
        start_position="pos-1",
        advanced_at=first,
    )
    store.record_boundary_advance(
        lease_id="lease_1",
        generation=1,
        kind="judge",
        superseded_invocation="el-2",
        start_position="pos-2",
        advanced_at=second,
    )

    boundary = store.boundary("lease_1", 1, "judge")
    assert boundary is not None
    assert boundary.start_position == "pos-original"
    assert boundary.opened_at == _T0
    current = store.current_start("lease_1", 1, "judge")
    assert current is not None
    assert (current.start_position, current.start_unreadable, current.at) == ("pos-2", False, second)
    assert len(store.open_boundaries_for_lease("lease_1")) == 1


def test_current_start_is_the_marker_when_there_is_no_advance_and_none_when_unopened() -> None:
    store = make_store("sqlite://")
    assert store.current_start("lease_1", 1, "judge") is None

    _open_judge(store)

    current = store.current_start("lease_1", 1, "judge")
    assert current is not None
    assert (current.start_position, current.start_unreadable, current.at) == ("pos-original", False, _T0)


def test_a_replayed_advance_with_the_same_superseded_invocation_writes_nothing() -> None:
    store = make_store("sqlite://")
    _open_judge(store)
    store.record_boundary_advance(
        lease_id="lease_1",
        generation=1,
        kind="judge",
        superseded_invocation="el-1",
        start_position="pos-1",
        advanced_at=_T0.replace(hour=1),
    )
    store.record_boundary_advance(
        lease_id="lease_1",
        generation=1,
        kind="judge",
        superseded_invocation="el-1",
        start_position="pos-replayed",
        advanced_at=_T0.replace(hour=2),
    )

    current = store.current_start("lease_1", 1, "judge")
    assert current is not None
    assert (current.start_position, current.at) == ("pos-1", _T0.replace(hour=1))


def test_advance_persists_start_unreadable() -> None:
    store = make_store("sqlite://")
    _open_judge(store)

    store.record_boundary_advance(
        lease_id="lease_1",
        generation=1,
        kind="judge",
        superseded_invocation="el-1",
        start_position=None,
        start_unreadable=True,
        advanced_at=_T0,
    )

    current = store.current_start("lease_1", 1, "judge")
    assert current is not None
    assert current.start_position is None
    assert current.start_unreadable is True


def test_advance_on_an_unopened_boundary_is_a_no_op() -> None:
    store = make_store("sqlite://")
    store.record_boundary_advance(
        lease_id="lease_1",
        generation=1,
        kind="judge",
        superseded_invocation="el-1",
        start_position="pos-a",
        advanced_at=_T0,
    )

    assert store.boundary("lease_1", 1, "judge") is None
    assert store.current_start("lease_1", 1, "judge") is None


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


def test_advance_on_a_closed_boundary_is_a_no_op() -> None:
    """A closed marker's history is final: an advance after its lease closed writes nothing,
    so ``current_start`` still answers the marker's own start."""
    store = make_store("sqlite://")
    _open_judge(store)
    store.close_boundaries_for_lease("lease_1", reason="completed", at=_T0.replace(hour=1))

    store.record_boundary_advance(
        lease_id="lease_1",
        generation=1,
        kind="judge",
        superseded_invocation="el-1",
        start_position="pos-late",
        advanced_at=_T0.replace(hour=2),
    )

    current = store.current_start("lease_1", 1, "judge")
    assert current is not None
    assert (current.start_position, current.at) == ("pos-original", _T0)


def test_open_after_close_keeps_the_closed_marker() -> None:
    store = make_store("sqlite://")
    _open_judge(store)
    store.close_boundaries_for_lease("lease_1", reason="completed", at=_T0.replace(hour=1))

    _open_judge(store, position="pos-reopened")

    boundary = store.boundary("lease_1", 1, "judge")
    assert boundary is not None
    assert (boundary.start_position, boundary.closed_reason) == ("pos-original", "completed")


def _open(store, lease_id: str, generation: int, kind: str) -> None:  #
    store.record_boundary_open(
        lease_id=lease_id,
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=generation,
        kind=kind,
        start_position=f"open-{lease_id}-{generation}-{kind}",
        opened_at=_T0,
    )


def _advance(store, lease_id: str, generation: int, kind: str, superseded: str, at: datetime) -> None:  #
    store.record_boundary_advance(
        lease_id=lease_id,
        generation=generation,
        kind=kind,
        superseded_invocation=superseded,
        start_position=f"adv-{lease_id}-{generation}-{kind}-{superseded}",
        advanced_at=at,
    )


def test_advances_are_independent_across_lease_generation_and_kind() -> None:
    """The same superseded invocation advances each ``(lease, generation, kind)`` on its own —
    neither the replay check nor ``current_start`` reads a sibling's advance."""
    store = make_store("sqlite://")
    markers = [("lease_1", 1, "judge"), ("lease_2", 1, "judge"), ("lease_1", 2, "judge"), ("lease_1", 1, "nudge")]
    for lease_id, generation, kind in markers:
        _open(store, lease_id, generation, kind)
    at = _T0.replace(hour=1)

    for lease_id, generation, kind in markers:
        _advance(store, lease_id, generation, kind, "el-1", at)

    for lease_id, generation, kind in markers:
        current = store.current_start(lease_id, generation, kind)  # type: ignore[arg-type]
        assert current is not None
        assert (current.start_position, current.at) == (f"adv-{lease_id}-{generation}-{kind}-el-1", at)


def test_a_sibling_advance_leaves_a_marker_without_its_own_advance_at_its_open_start() -> None:
    store = make_store("sqlite://")
    _open(store, "lease_1", 1, "judge")
    _open(store, "lease_2", 1, "judge")
    _open(store, "lease_1", 2, "judge")
    _open(store, "lease_1", 1, "nudge")

    _advance(store, "lease_1", 1, "judge", "el-1", _T0.replace(hour=1))

    for lease_id, generation, kind in [("lease_2", 1, "judge"), ("lease_1", 2, "judge"), ("lease_1", 1, "nudge")]:
        current = store.current_start(lease_id, generation, kind)  # type: ignore[arg-type]
        assert current is not None
        assert (current.start_position, current.at) == (f"open-{lease_id}-{generation}-{kind}", _T0)


def test_current_start_answers_the_latest_advanced_at_not_the_latest_recorded() -> None:
    store = make_store("sqlite://")
    _open(store, "lease_1", 1, "judge")
    later, earlier = _T0.replace(hour=2), _T0.replace(hour=1)

    _advance(store, "lease_1", 1, "judge", "el-2", later)
    _advance(store, "lease_1", 1, "judge", "el-1", earlier)

    current = store.current_start("lease_1", 1, "judge")
    assert current is not None
    assert (current.start_position, current.at) == ("adv-lease_1-1-judge-el-2", later)


def test_a_same_instant_advance_tie_answers_the_later_recorded() -> None:
    store = make_store("sqlite://")
    _open(store, "lease_1", 1, "judge")
    at = _T0.replace(hour=1)

    _advance(store, "lease_1", 1, "judge", "el-1", at)
    _advance(store, "lease_1", 1, "judge", "el-2", at)

    current = store.current_start("lease_1", 1, "judge")
    assert current is not None
    assert current.start_position == "adv-lease_1-1-judge-el-2"
