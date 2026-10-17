"""ApplyService and DecisionService under the row lock (unit tier).

The fake lock yields a handle whose ``facts`` are the post-race facts, while the pre-lock
``load_facts`` answers the pre-race ones — so each case observes only what the service re-derives
under the lock: a racer's landed movement answered as its replay, or a racer that moved the
chunk, escalated, or asked at the same epoch refused before any write. The ``record_*_locked``
writers are spies; every other port raises on an unexpected call."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.migration_source import MigrationSource
from blizzard.foundation.node_steps import ApplyOutcome, Executor
from blizzard.hub.domain.chunk.model import (
    Chunk,
    ChunkFacts,
    EscalationFact,
    IntendedMigration,
    QuestionFact,
    TransitionFact,
)
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.decisions import IWriteChunkDecisionsRepository
from blizzard.hub.domain.chunk.ports.escalations import IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites, ILockedChunkRead
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission, FenceRefusal
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.chunk.ports.route import IReadChunkRouteRepository, IWriteChunkRouteRepository
from blizzard.hub.domain.execution.apply import ApplyResult, ApplyService
from blizzard.hub.domain.execution.completion import MigrationTargets
from blizzard.hub.domain.execution.decisions import DecisionService
from blizzard.hub.domain.execution.submissions import GateSubmission
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL, Graph, Node
from blizzard.hub.domain.runners.registration import RetiredRunnerGuard
from tests.test_completion_rules import _AT_BUILD, _BUILD, _CHUNK, _DELIVERY, _GRAPH, _T1, _migration, _submission

pytestmark = pytest.mark.unit

_LOCK_WAIT = timedelta(seconds=5)
_HAND_OFF = MigrationTargets(cross_graph=_DELIVERY, intended=None, follow_latest=None)
_D_BUILD = "nd_d_build"
_D_LAND = "nd_d_land"


def _raise(port: str, name: str) -> Any:
    raise NotImplementedError(f"the service should not touch {port}.{name!r}")


@dataclass
class _Ports:
    """Every port the services hold. Only the probes and the ``*_locked`` spies answer."""

    pre_lock: ChunkFacts
    migrations: list[dict[str, Any]] = field(default_factory=list)
    transitions: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    hub_runs: list[tuple[str, str, int]] = field(default_factory=list)
    refusal: FenceRefusal | None = None

    # facts
    def load_facts(self, chunk_id: str) -> ChunkFacts:
        return self.pre_lock

    # movement
    def accepted_migration(self, chunk_id: str, *, from_node_id: str, epoch: int) -> bool:
        return False

    def accepted_transition_target(self, chunk_id: str, *, from_node_id: str, epoch: int) -> str | None:
        return None

    def record_migration_locked(self, handle: ILockedChunkRead, chunk_id: str, **kwargs: Any) -> str | FenceRefusal:
        self.migrations.append({"chunk_id": chunk_id, **kwargs})
        return self.refusal or "mg_recorded"

    def record_transition_locked(self, handle: ILockedChunkRead, **kwargs: Any) -> FenceRefusal | None:
        self.transitions.append(kwargs)
        return self.refusal

    # decisions
    def find_decision(self, chunk_id: str, *, node_id: str, epoch: int) -> None:
        return None

    def record_decision_locked(self, handle: ILockedChunkRead, **kwargs: Any) -> bool | FenceRefusal:
        self.decisions.append(kwargs)
        return self.refusal or True

    # route / artifacts / registry / hub executor
    def route_of(self, chunk_id: str) -> None:
        return None

    def load_artifacts(self, chunk_id: str) -> list[Any]:
        return []

    def is_retired(self, runner_id: str) -> bool:
        return False

    def run(self, chunk: Chunk, graph: Graph, node: Node, *, epoch: int) -> None:
        self.hub_runs.append((graph.graph_id, node.node_id, epoch))

    @property
    def writes(self) -> list[dict[str, Any]]:
        return [*self.migrations, *self.transitions, *self.decisions]

    def __getattr__(self, name: str) -> Any:
        return _raise("port", name)


@dataclass
class _LockedRead:
    chunk_facts: ChunkFacts

    def facts(self, chunk_id: str) -> ChunkFacts:
        return self.chunk_facts

    def __getattr__(self, name: str) -> Any:
        return _raise("handle", name)


@dataclass
class _FakeExclusiveWrites:
    under_lock: ChunkFacts
    clock: FixedClock

    @contextmanager
    def locked(self, chunk_ids: Sequence[str]) -> Iterator[ILockedChunkRead]:
        self.clock.advance(_LOCK_WAIT)  # time passes while the lock is awaited
        yield cast(ILockedChunkRead, _LockedRead(self.under_lock))


def _wired(under_lock: ChunkFacts, *, pre_lock: ChunkFacts = _AT_BUILD) -> tuple[_Ports, FixedClock, Any]:
    assert under_lock != pre_lock, "a race case needs the lock to see facts the pre-lock read did not"
    ports = _Ports(pre_lock=pre_lock)
    clock = FixedClock(_T1 + timedelta(hours=1))
    return ports, clock, cast(IChunkExclusiveWrites, _FakeExclusiveWrites(under_lock, clock))


def _apply_service(under_lock: ChunkFacts) -> tuple[ApplyService, _Ports, FixedClock]:
    ports, clock, exclusive = _wired(under_lock)
    service = ApplyService(
        facts=cast(IReadChunkFactsRepository, ports),
        movement=cast(IWriteChunkMovementRepository, ports),
        decisions=cast(IWriteChunkDecisionsRepository, ports),
        escalations=cast(IWriteChunkEscalationsRepository, ports),
        exclusive=exclusive,
        route=cast(IReadChunkRouteRepository, ports),
        artifacts=cast(IReadChunkArtifactsRepository, ports),
        retired=RetiredRunnerGuard(registry=cast(Any, ports)),
        clock=clock,
        hub_node_executor=ports,
        label=lambda _ref: None,
    )
    return service, ports, clock


def _decision_service(under_lock: ChunkFacts) -> tuple[DecisionService, _Ports, FixedClock]:
    ports, clock, exclusive = _wired(under_lock)
    service = DecisionService(
        facts=cast(IReadChunkFactsRepository, ports),
        route=cast(IWriteChunkRouteRepository, ports),
        decisions=cast(IWriteChunkDecisionsRepository, ports),
        exclusive=exclusive,
        retired=RetiredRunnerGuard(registry=cast(Any, ports)),
        clock=clock,
    )
    return service, ports, clock


def _hand_off(under_lock: ChunkFacts) -> tuple[ApplyResult, _Ports, FixedClock]:
    service, ports, clock = _apply_service(under_lock)
    return service.apply(_CHUNK, _GRAPH, _submission("hand-off", lease_id="lease_1"), targets=_HAND_OFF), ports, clock


def _ship(under_lock: ChunkFacts, *, chunk: Chunk = _CHUNK, targets: MigrationTargets | None = None) -> Any:
    service, ports, clock = _apply_service(under_lock)
    return service.apply(chunk, _GRAPH, _submission("ship", lease_id="lease_1"), targets=targets), ports, clock


# --- the racer's state the lock reveals ---------------------------------------------------------

_LATER = _T1 + timedelta(minutes=5)
_MOVED = replace(
    _AT_BUILD,
    transitions=[TransitionFact(to_node_id="nd_review", to_node_executor=Executor.RUNNER, epoch=1, recorded_at=_LATER)],
)
_ESCALATED = replace(_AT_BUILD, escalations=[EscalationFact(epoch=1, recorded_at=_LATER)])
_ASKED = replace(_AT_BUILD, questions=[QuestionFact(question_id="qn_racer", asked_at=_LATER, epoch=1)])
_INCOHERENT = pytest.mark.parametrize(
    ("under_lock", "detail"),
    [
        (_MOVED, "not the chunk's current node `review` at epoch 1"),
        (_ESCALATED, "the attempt at epoch 1 escalated"),
        (_ASKED, "question qn_racer is open"),
    ],
    ids=["moved", "escalated", "asked"],
)


def _transitioned_from_build(to_node_id: str) -> ChunkFacts:
    raced = TransitionFact(
        to_node_id=to_node_id, to_node_executor=Executor.RUNNER, epoch=1, recorded_at=_LATER, from_node_id="nd_build"
    )
    return replace(_AT_BUILD, transitions=[raced])


def _migrated_from_build(executor: Executor = Executor.RUNNER) -> ChunkFacts:
    return replace(_AT_BUILD, migrations=[_migration(MigrationSource.AUTHORED_EDGE, executor)])


# --- _land_migration: a fresh migration ---------------------------------------------------------


def test_a_fresh_cross_graph_migration_records_once_with_the_landing_and_the_claimant() -> None:
    # The lock sees an unrelated later lease — no movement at (nd_build, 1).
    result, ports, clock = _hand_off(replace(_AT_BUILD, promoted=False))

    assert ports.migrations == [
        {
            "chunk_id": "chk_1",
            "from_node_id": "nd_build",
            "from_graph_id": "gr_a",
            "to_graph_id": "gr_d",
            "landed_node_id": _D_BUILD,
            "choice_name": "hand-off",
            "decision_id": None,
            "model": "opus",
            "source": MigrationSource.AUTHORED_EDGE,
            "epoch": 1,
            "admission": EpochAdmission.CURRENT,
            "claimant": Claimant("runner-a", "lease_1"),
            "at": clock.now(),
            "artifacts": [],
            "release_route": True,
            "clear_intent": False,
            "migration_id": ports.migrations[0]["migration_id"],
        }
    ]
    assert ports.migrations[0]["migration_id"].startswith("mg_")
    assert (result.outcome, result.migration_id) == (ApplyOutcome.MIGRATED, "mg_recorded")
    assert result.fresh_migration
    assert ports.hub_runs == []


def test_a_migration_landing_on_a_hub_node_keeps_the_route_and_hands_the_node_to_the_hub() -> None:
    chunk = replace(_CHUNK, intended_migration=IntendedMigration.toward("gr_d", "land"))
    targets = MigrationTargets(cross_graph=None, intended=_DELIVERY, follow_latest=None)

    result, ports, _clock = _ship(replace(_AT_BUILD, promoted=False), chunk=chunk, targets=targets)

    assert [(m["landed_node_id"], m["release_route"], m["clear_intent"]) for m in ports.migrations] == [
        (_D_LAND, False, True)
    ]
    assert ports.hub_runs == [("gr_d", _D_LAND, 1)]
    assert (result.outcome, result.migration_id) == (ApplyOutcome.HUB_NODE_TAKEN, "mg_recorded")
    assert not result.fresh_migration  # only a MIGRATED answer is a fresh migration


def test_a_fenced_migration_write_answers_the_refusal() -> None:
    service, ports, _clock = _apply_service(replace(_AT_BUILD, promoted=False))
    ports.refusal = FenceRefusal.stale(1, latest=2)

    result = service.apply(_CHUNK, _GRAPH, _submission("hand-off"), targets=_HAND_OFF)

    assert (result.outcome, result.detail) == (ApplyOutcome.FAILURE, "stale epoch 1; chunk is at 2")
    assert len(ports.migrations) == 1


# --- races at the same (node, epoch): answered as the replay, never written twice ---------------


def test_a_migration_racing_a_landed_transition_answers_the_transitions_replay() -> None:
    result, ports, _clock = _hand_off(_transitioned_from_build(RESERVED_TERMINAL))

    assert ports.writes == []
    assert (result.outcome, result.transition_id, result.migration_id) == (ApplyOutcome.DONE, None, None)


@pytest.mark.parametrize(
    ("executor", "outcome"),
    [(Executor.RUNNER, ApplyOutcome.MIGRATED), (Executor.HUB, ApplyOutcome.HUB_NODE_TAKEN)],
    ids=["runner_landing", "hub_landing"],
)
def test_a_duplicate_migration_answers_as_a_replay_with_no_second_write(
    executor: Executor, outcome: ApplyOutcome
) -> None:
    result, ports, _clock = _hand_off(_migrated_from_build(executor))

    assert ports.writes == []
    assert ports.hub_runs == []
    assert (result.outcome, result.migration_id) == (outcome, None)
    assert not result.fresh_migration


def test_a_plain_completion_racing_a_landed_migration_answers_the_migrations_replay() -> None:
    result, ports, _clock = _ship(_migrated_from_build())

    assert ports.writes == []
    assert (result.outcome, result.detail, result.transition_id) == (
        ApplyOutcome.MIGRATED,
        "chunk already migrated (replay)",
        None,
    )


# --- in-lock attempt coherence: a racer at the same epoch refuses the write ---------------------


@_INCOHERENT
def test_a_migration_is_refused_under_the_lock_when_a_racer_left_the_attempt_incoherent(
    under_lock: ChunkFacts, detail: str
) -> None:
    result, ports, _clock = _hand_off(under_lock)

    assert ports.writes == []
    assert result.outcome is ApplyOutcome.FAILURE
    assert result.detail is not None and detail in result.detail


@_INCOHERENT
def test_a_plain_transition_is_refused_under_the_lock_when_a_racer_left_the_attempt_incoherent(
    under_lock: ChunkFacts, detail: str
) -> None:
    result, ports, _clock = _ship(under_lock)

    assert ports.writes == []
    assert result.outcome is ApplyOutcome.FAILURE
    assert detail in result.detail


@_INCOHERENT
def test_a_gate_decision_is_refused_under_the_lock_when_a_racer_left_the_attempt_incoherent(
    under_lock: ChunkFacts, detail: str
) -> None:
    service, ports, _clock = _decision_service(under_lock)

    result = service.submit(_CHUNK, _GRAPH, GateSubmission(from_node_id="nd_build", epoch=1, runner_id="runner-a"))

    assert ports.writes == []
    assert result.outcome is ApplyOutcome.FAILURE
    assert detail in result.detail


def test_a_coherent_gate_decision_records_once_under_the_lock() -> None:
    service, ports, clock = _decision_service(replace(_AT_BUILD, promoted=False))

    result = service.submit(
        _CHUNK, _GRAPH, GateSubmission(from_node_id="nd_build", epoch=1, runner_id="runner-a", lease_id="lease_1")
    )

    assert [(d["node_id"], d["epoch"], d["claimant"], d["at"]) for d in ports.decisions] == [
        (_BUILD.node_id, 1, Claimant("runner-a", "lease_1"), clock.now())
    ]
    assert result.outcome is ApplyOutcome.PARKED_AT_GATE
    assert result.decision_id == ports.decisions[0]["decision_id"]
