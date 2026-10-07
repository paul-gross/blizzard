"""``ChunkFacts.latest_movement`` and its ``as_of`` bound (unit tier) — facts only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.chunk.model import (
    ChunkFacts,
    MigrationFact,
    Movement,
    MovementKind,
    RestartFact,
    TransitionFact,
)

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _at(minutes: int) -> datetime:
    return _T0 + timedelta(minutes=minutes)


def _transition(node: str, *, minute: int, epoch: int = 1, graph_id: str | None = "gr_a") -> TransitionFact:
    return TransitionFact(node, Executor.RUNNER, epoch, _at(minute), graph_id=graph_id)


def _migration(node: str, *, minute: int, epoch: int = 1) -> MigrationFact:
    return MigrationFact(
        from_node_id=None,
        from_graph_id="gr_a",
        to_graph_id="gr_b",
        landed_node_id=node,
        choice_name=None,
        model=None,
        epoch=epoch,
        recorded_at=_at(minute),
    )


def _restart(node: str, *, minute: int, epoch: int = 1) -> RestartFact:
    return RestartFact(to_node_id=node, from_node_id=None, graph_id="gr_c", epoch=epoch, recorded_at=_at(minute))


def test_an_unbounded_read_is_the_newest_movement_of_any_family() -> None:
    facts = ChunkFacts(
        minted=True, transitions=[_transition("nd_1", minute=1)], migrations=[_migration("nd_2", minute=2)]
    )

    assert facts.latest_movement() == Movement(MovementKind.MIGRATION, "nd_2", Executor.RUNNER, "gr_b")


def test_a_chunk_that_never_moved_has_no_movement() -> None:
    assert ChunkFacts(minted=True).latest_movement() is None
    assert ChunkFacts(minted=True, transitions=[_transition("nd_1", minute=5)]).latest_movement(as_of=_at(1)) is None


def test_the_bound_falls_back_to_an_older_fact_of_a_family_whose_newest_is_later() -> None:
    facts = ChunkFacts(
        minted=True,
        transitions=[_transition("nd_old", minute=1), _transition("nd_new", minute=9, epoch=2)],
    )

    movement = facts.latest_movement(as_of=_at(5))

    assert movement == Movement(MovementKind.TRANSITION, "nd_old", Executor.RUNNER, "gr_a")


def test_the_bound_is_inclusive_and_a_later_family_does_not_displace_it() -> None:
    facts = ChunkFacts(
        minted=True,
        transitions=[_transition("nd_1", minute=3)],
        migrations=[_migration("nd_2", minute=7)],
    )

    assert facts.latest_movement(as_of=_at(3)) == Movement(MovementKind.TRANSITION, "nd_1", Executor.RUNNER, "gr_a")
    assert facts.latest_movement(as_of=_at(7)) == Movement(MovementKind.MIGRATION, "nd_2", Executor.RUNNER, "gr_b")


def test_the_kind_rank_breaks_a_tie_at_the_bound() -> None:
    facts = ChunkFacts(
        minted=True,
        transitions=[_transition("nd_t", minute=4, epoch=2)],
        migrations=[_migration("nd_m", minute=4, epoch=2)],
        restarts=[_restart("nd_r", minute=4, epoch=2)],
    )

    assert facts.latest_movement(as_of=_at(4)) == Movement(MovementKind.RESTART, "nd_r", Executor.RUNNER, "gr_c")
    without_restart = ChunkFacts(minted=True, transitions=facts.transitions, migrations=facts.migrations)
    assert without_restart.latest_movement(as_of=_at(4)) == Movement(
        MovementKind.MIGRATION, "nd_m", Executor.RUNNER, "gr_b"
    )


def test_a_transition_without_a_graph_carries_none() -> None:
    facts = ChunkFacts(minted=True, transitions=[_transition("nd_1", minute=1, graph_id=None)])

    movement = facts.latest_movement()

    assert movement is not None and movement.graph_id is None
