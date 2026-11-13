"""The reconciler's close and turn units (unit tier): each declares its submission's chunk and a lock per flow it holds
— once, before any read — re-reads the standing in that unit, decides there, and writes with the locked token; a
re-read flow outside the declared keys writes nothing and runs the unit again over the union."""

from __future__ import annotations

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, cast

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.flow_writes import FlowFactWritten
from blizzard.hub.domain.delivery.flows import Flow, LandingTerms, QueueStanding, Submission, Turn, flow_lock
from blizzard.hub.domain.delivery.reconcile import FlowLineReconciler
from blizzard.hub.domain.kernel.locks import LockKey
from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction

_AT = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
_MAIN = Flow(flow_id="flw_main", repository_id="repo_a", branch="main", created_at=_AT)
_OTHER = Flow(flow_id="flw_other", repository_id="repo_b", branch="master", created_at=_AT)
_SUB = Submission(
    submission_id="fsb_1",
    chunk_id="ch_1",
    node_id="nd_deliver",
    epoch=1,
    generation=0,
    arrival=1,
    submitted_at=_AT,
    flows=(_MAIN,),
)
_FREE = QueueStanding(submission=_SUB, turning=False, busy=False, ahead=frozenset())


class _Line:
    def eligible(self, sub: Submission, *, now: datetime) -> bool:
        return True

    def is_next(self, sub: Submission, *, now: datetime) -> bool:
        return True


@dataclass
class _World:
    """One fake per seam the reconciler reaches. ``standings`` answers the standing re-reads in turn; ``events`` orders
    each unit's declaration against its read and its write, every one of which must name the unit's locked token."""

    standings: list[QueueStanding | None]
    events: list[str] = field(default_factory=list[str])
    declared: list[tuple[list[str], list[LockKey]]] = field(default_factory=list[tuple[list[str], list[LockKey]]])
    token: object = None

    @contextmanager
    def begin(self) -> Generator[Transaction]:
        yield cast(Transaction, self)

    def lock_set(self, *, chunks: Sequence[str] = (), keys: Sequence[LockKey] = ()) -> LockedTransaction:
        self.declared.append((list(chunks), list(keys)))
        self.events.append("lock")
        self.token = object()
        return cast(LockedTransaction, self.token)

    def _in_unit(self, event: str, transaction: object) -> None:
        assert transaction is self.token, f"{event} ran outside the unit"
        self.events.append(event)

    # --- flows ---
    def open_submissions_from(self, floor: int) -> list[Submission]:
        return [_SUB]

    def open_turns(self) -> list[Turn]:
        return []

    def queue_standing(self, submission_id: str, *, transaction: Transaction) -> QueueStanding | None:
        self._in_unit("standing", transaction)
        return self.standings.pop(0)

    def take_turn(
        self, submission_id: str, *, terms: Sequence[LandingTerms], at: datetime, transaction: LockedTransaction
    ) -> Turn:
        self._in_unit("take_turn", transaction)
        return Turn(turn_id="htn_1", submission_id=submission_id, started_at=at)

    def close(
        self, submission_id: str, *, outcome: str, at: datetime, transaction: LockedTransaction
    ) -> tuple[FlowFactWritten, ...] | None:
        self._in_unit("close", transaction)
        return ()

    # --- the pass's other reads, all empty ---
    def line(self, submissions: Sequence[Submission], *, turning: frozenset[str]) -> _Line:
        return _Line()

    def list_all(self, *, include_retired: bool) -> list[Any]:
        return []

    def get_many(self, ids: Sequence[str]) -> dict[str, Any]:
        return {}

    def load_live_statuses(self) -> dict[str, Any]:
        return {}

    def load_facts_for(self, chunk_ids: Sequence[str]) -> dict[str, Any]:
        return {}

    def load_artifacts_for(self, chunk_ids: Sequence[str]) -> dict[str, Any]:
        return {}


def _reconciler(world: _World) -> FlowLineReconciler:
    seam = cast(Any, world)
    return FlowLineReconciler(
        flows=seam,
        facts=seam,
        record=seam,
        graphs=seam,
        artifacts=seam,
        repositories=seam,
        forge_records=seam,
        projects=seam,
        links=seam,
        targets=seam,
        catalogue=seam,
        refusals=seam,
        transactions=seam,
        lines=seam,
        clock=FixedClock(_AT),
    )


def test_a_turn_declares_its_chunk_and_flows_then_reads_and_writes_in_that_unit() -> None:
    world = _World(standings=[_FREE])

    admitted = _reconciler(world).admit(0, _AT)

    assert [a.turn.turn_id for a in admitted] == ["htn_1"]
    assert world.declared == [(["ch_1"], [flow_lock(_MAIN)])]
    assert world.events == ["lock", "standing", "take_turn"]


def test_a_turn_the_re_read_refuses_writes_nothing() -> None:
    world = _World(standings=[replace(_FREE, busy=True)])

    assert _reconciler(world).admit(0, _AT) == []
    assert world.events == ["lock", "standing"]


def test_a_close_declares_its_chunk_and_flows_then_reads_and_writes_in_that_unit() -> None:
    world = _World(standings=[_FREE])

    result = _reconciler(world).reconcile(0)

    assert result.closed == 1
    assert world.declared == [(["ch_1"], [flow_lock(_MAIN)])]
    assert world.events == ["lock", "standing", "close"]


def test_a_close_the_re_read_finds_turning_writes_nothing() -> None:
    world = _World(standings=[replace(_FREE, turning=True)])

    assert _reconciler(world).reconcile(0).closed == 0
    assert world.events == ["lock", "standing"]


def test_a_submission_the_re_read_does_not_find_writes_nothing() -> None:
    world = _World(standings=[None])

    assert _reconciler(world).admit(0, _AT) == []
    assert world.events == ["lock", "standing"]


def test_a_re_read_flow_outside_the_declared_keys_reruns_the_unit_over_the_union() -> None:
    wider = replace(_FREE, submission=replace(_SUB, flows=(_MAIN, _OTHER)))
    world = _World(standings=[wider, wider])

    admitted = _reconciler(world).admit(0, _AT)

    assert [a.turn.turn_id for a in admitted] == ["htn_1"]
    assert world.declared == [
        (["ch_1"], [flow_lock(_MAIN)]),
        (["ch_1"], sorted([flow_lock(_MAIN), flow_lock(_OTHER)])),
    ]
    assert world.events == ["lock", "standing", "lock", "standing", "take_turn"]


def test_a_close_reruns_over_the_union_too() -> None:
    wider = replace(_FREE, submission=replace(_SUB, flows=(_MAIN, _OTHER)))
    world = _World(standings=[wider, wider])

    assert _reconciler(world).reconcile(0).closed == 1
    assert [keys for _, keys in world.declared] == [[flow_lock(_MAIN)], sorted([flow_lock(_MAIN), flow_lock(_OTHER)])]
    assert world.events == ["lock", "standing", "lock", "standing", "close"]
