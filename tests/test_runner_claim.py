"""Claiming a chunk's route — ``ReadyQueue.claim_one`` off the ready queue, and
``InterruptedClaims.reconcile`` re-claiming a binding whose claim never landed (unit tier).

Each claim outcome is scripted at the hub seam; what a claim came to is read back through the
production outputs only — the return value, the next claim the queue makes, the held bindings,
the chunk-view invalidations, and the structured log."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, MutableMapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.fact_kinds import RUNNER_LOCALLY_PAUSED
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.hub.chunk_status_cache import ReadThroughChunkViews
from blizzard.runner.hub.client import (
    ClaimConflict,
    ClaimRequest,
    DependencyDenial,
    IncompatibleDenial,
    PausedDenial,
    QueueEntry,
    RouteClaimOutcome,
    TerminalDenial,
)
from blizzard.runner.lifecycle.claim import InterruptedClaims, ReadyQueue
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.node_steps.chunk_state import ChunkPause, ChunkState
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    claimed_outcome,
    make_context,
    make_envelope,
    make_store,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]
_SPAWN_SUPPRESSED = "spawn suppressed — locally paused"
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)


class _ScriptedClaimHub(FakeHub):
    """Each ``claim_route`` pops the next scripted outcome."""

    def __init__(self, outcomes: Iterable[RouteClaimOutcome]) -> None:
        super().__init__()
        self.outcomes = list(outcomes)

    def claim_route(self, claim: ClaimRequest) -> RouteClaimOutcome:
        self.claims.append(claim)
        return self.outcomes.pop(0)


@dataclass
class _RecordingChunkViews:
    """Reads through to the hub; records every invalidation."""

    hub: FakeHub
    invalidated: list[str] = field(default_factory=list)

    def get(self, chunk_id: str) -> ChunkState:
        return ReadThroughChunkViews(hub=self.hub).get(chunk_id)

    def prime(self, chunk_ids: Iterable[str]) -> None:
        ReadThroughChunkViews(hub=self.hub).prime(chunk_ids)

    def invalidate(self, chunk_id: str) -> None:
        self.invalidated.append(chunk_id)


def _won(chunk_id: str = "ch_1") -> RouteClaimOutcome:
    return claimed_outcome(chunk_id, make_envelope(chunk_id, "build", node_id="nd_build", choices=_CHOICES))


def _lost(chunk_id: str = "ch_1") -> RouteClaimOutcome:
    return RouteClaimOutcome(conflict=ClaimConflict(chunk_id=chunk_id, held_by_runner_id="r2"))


def _paused(chunk_id: str = "ch_1") -> RouteClaimOutcome:
    return RouteClaimOutcome(denied_paused=PausedDenial(chunk_id=chunk_id, runner_id="r1", detail="runner retired"))


def _not_claimable(chunk_id: str = "ch_1") -> RouteClaimOutcome:
    return RouteClaimOutcome(denied_terminal=TerminalDenial(chunk_id=chunk_id, status="done", detail="chunk is done"))


def _dependency(chunk_id: str = "ch_1") -> RouteClaimOutcome:
    return RouteClaimOutcome(denied_dependency=DependencyDenial(chunk_id=chunk_id, prerequisite_chunk_id="ch_0"))


def _incompatible(chunk_id: str = "ch_1") -> RouteClaimOutcome:
    return RouteClaimOutcome(denied_incompatible=IncompatibleDenial(chunk_id=chunk_id, incompatible_runner_id="r1"))


def _setup(
    outcomes: list[RouteClaimOutcome], *, strict: bool = False
) -> tuple[Any, _ScriptedClaimHub, _RecordingChunkViews, Any]:
    """A context whose local brake is engaged, so a won claim's spawn logs its ``via`` and stops."""
    store = make_store("sqlite://")
    hub = _ScriptedClaimHub(outcomes)
    views = _RecordingChunkViews(hub=hub)
    config = LoopConfig(runner_id="r1", workspace_id="ws1", max_agents=1, queue_strict=strict)
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1", "e2": "/ws/e2"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        config=config,
        chunk_views=views,
    )
    store.record_local_pause(
        "r1",
        paused=True,
        at=_NOW,
        by="operator",
        report_kind=RUNNER_LOCALLY_PAUSED,
        report_payload=json.dumps({"runner_id": "r1", "by": "operator"}),
    )
    return store, hub, views, ctx


def _records(logs: Sequence[MutableMapping[str, Any]], event: str) -> list[MutableMapping[str, Any]]:
    return [record for record in logs if record["event"] == event]


# ReadyQueue.claim_one


@pytest.mark.unit
@pytest.mark.parametrize(
    ("make_outcome", "strict", "returns", "next_claimed", "released", "loss_log"),
    [
        pytest.param(_won, False, True, "ch_2", False, None, id="won"),
        pytest.param(_lost, False, True, "ch_2", True, ("route claim lost the race", {}), id="lost"),
        pytest.param(
            _paused,
            False,
            False,
            "ch_2",
            True,
            ("route claim denied — the hub refused this runner", {"detail": "runner retired"}),
            id="paused",
        ),
        pytest.param(
            _not_claimable,
            False,
            True,
            "ch_2",
            True,
            ("route claim denied — chunk not claimable", {"status": "done", "detail": "chunk is done"}),
            id="not-claimable",
        ),
        pytest.param(
            _dependency,
            False,
            True,
            "ch_2",
            True,
            ("route claim denied — unmet prerequisite", {}),
            id="dependency-pass-over",
        ),
        pytest.param(
            _dependency,
            True,
            False,
            "ch_1",
            True,
            ("route claim denied — unmet prerequisite", {}),
            id="dependency-strict-holds-head",
        ),
        pytest.param(
            _incompatible,
            False,
            True,
            "ch_2",
            True,
            ("route claim denied — runner incompatible with chunk", {}),
            id="incompatible",
        ),
    ],
)
def test_claim_one_disposes_each_claim_outcome(  # type: ignore[no-untyped-def]
    make_outcome: Callable[[], RouteClaimOutcome],
    strict: bool,
    returns: bool,
    next_claimed: str,
    released: bool,
    loss_log: tuple[str, dict[str, str]] | None,
):
    store, hub, views, ctx = _setup([make_outcome(), _lost("ch_2")], strict=strict)
    hub.queue = [
        QueueEntry(chunk_id="ch_1", graph_id="gr_1", position=0),
        QueueEntry(chunk_id="ch_2", graph_id="gr_1", position=1),
    ]
    queue = ReadyQueue.peeked(ctx)

    with capture_logs() as logs:
        result = queue.claim_one()

    assert result is returns
    assert store.held_environment_ids() == ([] if released else ["e1"])
    assert views.invalidated == ([] if released else ["ch_1"])
    losses = [r for r in logs if r["event"].startswith("route claim")]
    if loss_log is None:
        assert losses == []
    else:
        message, denial_fields = loss_log
        assert losses == [
            {"event": message, "log_level": "info", "chunk_id": "ch_1", "runner_id": "r1", **denial_fields}
        ]

    queue.claim_one()  # the entry a strict dependency hold keeps is claimed again; any other is gone
    assert hub.claims[1].chunk_id == next_claimed


@pytest.mark.unit
def test_claim_one_enters_a_won_claim_via_fill():  # type: ignore[no-untyped-def]
    _store, hub, _views, ctx = _setup([_won()])
    hub.queue = [QueueEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]

    with capture_logs() as logs:
        ReadyQueue.peeked(ctx).claim_one()

    assert [r["via"] for r in _records(logs, _SPAWN_SUPPRESSED)] == ["fill"]


# InterruptedClaims.reconcile


def _bound(store: Any, hub: FakeHub, view: ChunkState) -> None:
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    hub.chunks["ch_1"] = view


@pytest.mark.unit
def test_reconcile_reclaims_an_unbraked_ready_binding_via_reclaim():  # type: ignore[no-untyped-def]
    store, hub, views, ctx = _setup([_won()])
    _bound(store, hub, ChunkState(chunk_id="ch_1", status=ChunkStatus.READY))

    with capture_logs() as logs:
        InterruptedClaims(ctx).reconcile()

    assert [c.environment_ids for c in hub.claims] == [["e1"]]
    assert views.invalidated == ["ch_1"]
    assert [r["via"] for r in _records(logs, _SPAWN_SUPPRESSED)] == ["reclaim"]
    assert store.held_environment_ids() == ["e1"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("view", "requeued", "braked"),
    [
        pytest.param(ChunkState(chunk_id="ch_1", status=ChunkStatus.READY), False, True, id="braked-ready"),
        pytest.param(
            ChunkState(
                chunk_id="ch_1",
                status=ChunkStatus.RUNNING,
                route_runner_id="r1",
                pause=ChunkPause(by="operator", set_at="2026-07-13T12:00:00Z"),
            ),
            True,
            False,
            id="requeued-ours-but-paused",
        ),
    ],
)
def test_reconcile_holds_a_binding_without_claiming(view: ChunkState, requeued: bool, braked: bool):  # type: ignore[no-untyped-def]
    store, hub, views, ctx = _setup([])
    _bound(store, hub, view)
    if requeued:
        store.record_requeue(chunk_id="ch_1", at=_NOW)

    with capture_logs() as logs:
        InterruptedClaims(ctx).reconcile(braked=braked)

    assert hub.claims == []
    assert store.held_environment_ids() == ["e1"]
    assert views.invalidated == []
    assert _records(logs, _SPAWN_SUPPRESSED) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("make_outcome", "message", "denial_fields"),
    [
        pytest.param(
            _paused,
            "interrupted claim denied — the hub refused this runner",
            {"detail": "runner retired"},
            id="paused",
        ),
        pytest.param(_lost, "interrupted claim not won — releasing binding", {}, id="lost"),
        pytest.param(
            _not_claimable,
            "interrupted claim not won — releasing binding",
            {"status": "done", "detail": "chunk is done"},
            id="not-claimable",
        ),
        pytest.param(_dependency, "interrupted claim not won — releasing binding", {}, id="dependency"),
        pytest.param(_incompatible, "interrupted claim not won — releasing binding", {}, id="incompatible"),
    ],
)
def test_reconcile_releases_a_reclaim_that_is_not_won(  # type: ignore[no-untyped-def]
    make_outcome: Callable[[], RouteClaimOutcome], message: str, denial_fields: dict[str, str]
):
    store, hub, views, ctx = _setup([make_outcome()])
    _bound(store, hub, ChunkState(chunk_id="ch_1", status=ChunkStatus.READY))

    with capture_logs() as logs:
        InterruptedClaims(ctx).reconcile()

    assert len(hub.claims) == 1
    assert store.held_environment_ids() == []
    assert views.invalidated == []
    assert _records(logs, message) == [{"event": message, "log_level": "info", "chunk_id": "ch_1", **denial_fields}]
