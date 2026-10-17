"""The SQL ``done`` predicates' tie and boundary shapes (unit tier, over a head-migrated store).

Each case seeds one shape the derivation settles by an exact comparison — a one-epoch-off tie, an
instant equal to ``finished_before``, a stop against a completion at the same instant, another
chunk's rows beside a done one — and asserts :func:`settled_done` / :func:`done_completed_at` read it
as :meth:`ChunkFacts.status` / :meth:`ChunkFacts.completed_at` do. Every instant comes off one
:class:`FixedClock`, so equal instants are bit-identical."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_terminal_predicates import done_completed_at, settled_done
from tests.support import chunk_stores, hub_store_connections, migrate_to, seed_chunk, seed_graph
from tests.support_chunk_world import complete, migration, restart, stop, transition

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 3, 1, tzinfo=UTC)
_AGO = timedelta(hours=1)
_MINUTE = timedelta(minutes=1)


@dataclass
class _World:
    engine: Engine
    clock: FixedClock

    def mint(self, *chunk_ids: str) -> None:
        with self.engine.begin() as conn:
            for chunk_id in chunk_ids:
                seed_chunk(conn, chunk_id, graph_id="gr_1", at=self.clock.now() - timedelta(days=1))

    def settled_done(self, *, finished_before: datetime | None = None) -> set[str]:
        connections = hub_store_connections(self.engine)
        stmt = connections.select(s.chunks, s.chunks.c.chunk_id).where(
            settled_done(connections, finished_before=finished_before)
        )
        with connections.read("settled_done") as conn:
            return set(conn.execute(stmt).scalars())

    def done_completed_at(self) -> dict[str, datetime]:
        connections = hub_store_connections(self.engine)
        stmt = connections.select(s.chunks, s.chunks.c.chunk_id, done_completed_at(connections)).where(
            settled_done(connections)
        )
        with connections.read("done_completed_at") as conn:
            return {chunk_id: _utc(at) for chunk_id, at in conn.execute(stmt).all()}

    def derived_done(self) -> set[str]:
        facts = chunk_stores(self.engine, self.clock).facts.load_all_facts()
        return {chunk_id for chunk_id, f in facts.items() if f.status() is ChunkStatus.DONE}

    def derived_completed_at(self) -> dict[str, datetime]:
        facts = chunk_stores(self.engine, self.clock).facts.load_all_facts()
        return {
            chunk_id: _utc(at)
            for chunk_id, f in facts.items()
            if f.status() is ChunkStatus.DONE and (at := f.completed_at()) is not None
        }


def _utc(at: datetime) -> datetime:
    return at if at.tzinfo is not None else at.replace(tzinfo=UTC)


@pytest.fixture
def world(tmp_path: Path) -> _World:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_NOW - timedelta(days=2))
    return _World(engine, FixedClock(_NOW))


def _terminal(world: _World, chunk_id: str, *, epoch: int = 1, ago: timedelta = _AGO) -> None:
    transition(world, chunk_id, RESERVED_TERMINAL, epoch=epoch, ago=ago)


# --- _done_by_transition / _unsuperseded_terminal ------------------------------------------------


def test_a_same_instant_movement_one_epoch_below_the_terminal_leaves_it_done(world: _World) -> None:
    world.mint("ch_tr", "ch_mg", "ch_rs")
    for chunk_id in ("ch_tr", "ch_mg", "ch_rs"):
        _terminal(world, chunk_id, epoch=2)
    transition(world, "ch_tr", "nd_1", epoch=1, ago=_AGO, n=1)
    migration(world, "ch_mg", epoch=1, ago=_AGO)
    restart(world, "ch_rs", epoch=1, ago=_AGO)

    assert world.settled_done() == world.derived_done() == {"ch_tr", "ch_mg", "ch_rs"}


def test_a_same_instant_migration_or_restart_one_epoch_above_the_terminal_supersedes_it(world: _World) -> None:
    world.mint("ch_tr", "ch_mg", "ch_rs")
    for chunk_id in ("ch_tr", "ch_mg", "ch_rs"):
        _terminal(world, chunk_id, epoch=1)
    transition(world, "ch_tr", "nd_1", epoch=2, ago=_AGO, n=1)
    migration(world, "ch_mg", epoch=2, ago=_AGO)
    restart(world, "ch_rs", epoch=2, ago=_AGO)

    assert world.settled_done() == world.derived_done() == set()


def test_a_same_instant_same_epoch_migration_or_restart_supersedes_the_terminal(world: _World) -> None:
    world.mint("ch_mg", "ch_rs")
    _terminal(world, "ch_mg")
    _terminal(world, "ch_rs")
    migration(world, "ch_mg", epoch=1, ago=_AGO)
    restart(world, "ch_rs", epoch=1, ago=_AGO)

    assert world.settled_done() == world.derived_done() == set()


def test_a_same_instant_same_epoch_non_terminal_transition_leaves_the_residue_live(world: _World) -> None:
    """The documented residue: the derivation orders this tie by fact order; the SQL leaves it live."""
    world.mint("ch_mixed")
    _terminal(world, "ch_mixed")
    transition(world, "ch_mixed", "nd_1", epoch=1, ago=_AGO, n=1)

    assert world.settled_done() == set()


def test_a_later_movement_at_a_lower_epoch_supersedes_the_terminal(world: _World) -> None:
    world.mint("ch_tr", "ch_mg", "ch_rs")
    for chunk_id in ("ch_tr", "ch_mg", "ch_rs"):
        _terminal(world, chunk_id, epoch=2)
    later = _AGO - _MINUTE
    transition(world, "ch_tr", "nd_1", epoch=1, ago=later, n=1)
    migration(world, "ch_mg", epoch=1, ago=later)
    restart(world, "ch_rs", epoch=1, ago=later)

    assert world.settled_done() == world.derived_done() == set()


def test_an_earlier_movement_at_a_higher_epoch_leaves_the_terminal_done(world: _World) -> None:
    world.mint("ch_tr", "ch_mg", "ch_rs")
    for chunk_id in ("ch_tr", "ch_mg", "ch_rs"):
        _terminal(world, chunk_id, epoch=1)
    earlier = _AGO + _MINUTE
    transition(world, "ch_tr", "nd_1", epoch=2, ago=earlier, n=1)
    migration(world, "ch_mg", epoch=2, ago=earlier)
    restart(world, "ch_rs", epoch=2, ago=earlier)

    assert world.settled_done() == world.derived_done() == {"ch_tr", "ch_mg", "ch_rs"}


def test_another_chunks_newer_movements_never_supersede_a_done_chunks_terminal(world: _World) -> None:
    world.mint("ch_done", "ch_other")
    _terminal(world, "ch_done")
    newer = _AGO - _MINUTE
    transition(world, "ch_other", "nd_1", epoch=2, ago=newer)
    migration(world, "ch_other", epoch=2, ago=newer)
    restart(world, "ch_other", epoch=3, ago=newer)

    assert world.settled_done() == world.derived_done() == {"ch_done"}


def test_a_terminal_transition_settles_only_when_recorded_strictly_before_finished_before(world: _World) -> None:
    world.mint("ch_at", "ch_before")
    _terminal(world, "ch_at", ago=_AGO)
    _terminal(world, "ch_before", ago=_AGO + timedelta(seconds=1))

    assert world.settled_done(finished_before=_NOW - _AGO) == {"ch_before"}


# --- settled_done: the completion and stop arms ---------------------------------------------------


def test_a_completion_settles_done_against_a_stop_at_the_equal_instant(world: _World) -> None:
    world.mint("ch_tie", "ch_stop_later")
    complete(world, "ch_tie", ago=_AGO)
    stop(world, "ch_tie", ago=_AGO)
    complete(world, "ch_stop_later", ago=_AGO)
    stop(world, "ch_stop_later", ago=_AGO - timedelta(seconds=1))

    assert world.settled_done() == world.derived_done() == {"ch_tie"}


def test_a_completion_settles_only_when_every_completion_is_strictly_before_finished_before(world: _World) -> None:
    world.mint("ch_at", "ch_before", "ch_one_late")
    complete(world, "ch_at", ago=_AGO)
    complete(world, "ch_before", ago=_AGO + timedelta(seconds=1))
    complete(world, "ch_one_late", ago=_AGO + _MINUTE)
    complete(world, "ch_one_late", ago=_AGO)

    assert world.settled_done(finished_before=_NOW - _AGO) == {"ch_before"}


def test_a_stop_or_completion_takes_a_terminal_transition_off_the_transition_arm(world: _World) -> None:
    world.mint("ch_stopped", "ch_completed_then_stopped")
    _terminal(world, "ch_stopped")
    stop(world, "ch_stopped", ago=_AGO - _MINUTE)
    _terminal(world, "ch_completed_then_stopped")
    complete(world, "ch_completed_then_stopped", ago=_AGO + _MINUTE)
    stop(world, "ch_completed_then_stopped", ago=_AGO)

    assert world.settled_done() == world.derived_done() == set()


def test_another_chunks_stop_or_completion_never_unsettles_a_done_chunk(world: _World) -> None:
    world.mint("ch_by_transition", "ch_by_completion", "ch_other")
    _terminal(world, "ch_by_transition")
    complete(world, "ch_by_completion", ago=_AGO)
    stop(world, "ch_other", ago=_AGO - _MINUTE)
    complete(world, "ch_other", ago=_AGO - 2 * _MINUTE)

    assert world.settled_done() == world.derived_done() == {"ch_by_transition", "ch_by_completion", "ch_other"}
    assert world.settled_done(finished_before=_NOW - _AGO + timedelta(seconds=1)) == {
        "ch_by_transition",
        "ch_by_completion",
    }


# --- done_completed_at ----------------------------------------------------------------------------


def test_the_completion_instant_is_the_newest_completion_else_the_unsuperseded_terminal(world: _World) -> None:
    world.mint("ch_completed", "ch_transitioned", "ch_both")
    complete(world, "ch_completed", ago=_AGO + _MINUTE)
    complete(world, "ch_completed", ago=_AGO)
    transition(world, "ch_transitioned", RESERVED_TERMINAL, epoch=1, ago=_AGO + 3 * _MINUTE)
    transition(world, "ch_transitioned", "nd_1", epoch=1, ago=_AGO + 2 * _MINUTE, n=1)
    transition(world, "ch_transitioned", RESERVED_TERMINAL, epoch=1, ago=_AGO, n=2)
    _terminal(world, "ch_both", ago=_AGO - _MINUTE)
    complete(world, "ch_both", ago=_AGO)

    expected = {"ch_completed": _NOW - _AGO, "ch_transitioned": _NOW - _AGO, "ch_both": _NOW - _AGO}
    assert world.done_completed_at() == world.derived_completed_at() == expected


def test_the_completion_instant_ranks_only_the_chunks_own_rows(world: _World) -> None:
    world.mint("ch_by_transition", "ch_by_completion", "ch_other")
    _terminal(world, "ch_by_transition", ago=_AGO)
    complete(world, "ch_by_completion", ago=_AGO + _MINUTE)
    newest = _AGO - 10 * _MINUTE
    complete(world, "ch_other", ago=newest)
    _terminal(world, "ch_other", ago=newest)

    assert (
        world.done_completed_at()
        == world.derived_completed_at()
        == {
            "ch_by_transition": _NOW - _AGO,
            "ch_by_completion": _NOW - _AGO - _MINUTE,
            "ch_other": _NOW - newest,
        }
    )
