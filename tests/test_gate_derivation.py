"""Gate/requeue status derivation — pure functions over facts (unit tier).

These pin the ``waiting_on_human`` branch and requeue supersession as pure queries over
an in-memory :class:`ChunkFacts` — no store, no tokens (``bzh:domain-takes-objects``).
The component tier (test_gates) proves the same on the wired hub.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.domain.graph import Executor
from blizzard.hub.domain.work import (
    ChunkFacts,
    ChunkStatus,
    DecisionFact,
    EscalationFact,
    LeaseFact,
    RequeueFact,
    RouteCreatedFact,
    RouteReleasedFact,
    TransitionFact,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _running_facts() -> ChunkFacts:
    """A claimed, live-route chunk — the base a gate decision parks."""
    return ChunkFacts(
        minted=True,
        leases=[LeaseFact(epoch=1, minted_at=_at(0))],
        routes_created=[RouteCreatedFact(created_at=_at(0))],
    )


def test_open_decision_parks_waiting_on_human() -> None:
    facts = ChunkFacts(
        minted=True,
        leases=[LeaseFact(epoch=1, minted_at=_at(0))],
        routes_created=[RouteCreatedFact(created_at=_at(0))],
        decisions=[DecisionFact(decision_id="dec_1", submitted_at=_at(1), resolved=False)],
    )
    assert facts.open_decision() is not None
    assert facts.status() is ChunkStatus.WAITING_ON_HUMAN


def test_resolved_decision_no_longer_waits() -> None:
    # Resolved but the resolving transition has not landed yet: the route is still live,
    # so the chunk derives running — "the resolution flips it back".
    facts = ChunkFacts(
        minted=True,
        leases=[LeaseFact(epoch=1, minted_at=_at(0))],
        routes_created=[RouteCreatedFact(created_at=_at(0))],
        decisions=[DecisionFact(decision_id="dec_1", submitted_at=_at(1), resolved=True)],
    )
    assert facts.open_decision() is None
    assert facts.status() is ChunkStatus.RUNNING


def test_needs_human_outranks_waiting_on_human() -> None:
    # An open escalation (needs_human) takes precedence over an open decision.
    facts = ChunkFacts(
        minted=True,
        leases=[LeaseFact(epoch=1, minted_at=_at(0))],
        routes_created=[RouteCreatedFact(created_at=_at(0))],
        escalations=[EscalationFact(epoch=1, recorded_at=_at(2))],
        decisions=[DecisionFact(decision_id="dec_1", submitted_at=_at(1), resolved=False)],
    )
    assert facts.status() is ChunkStatus.NEEDS_HUMAN


def test_gate_decision_derives_over_a_delivering_transition_is_not_possible() -> None:
    # A human gate node is runner-executed, so a transition into it is never a hub node;
    # waiting_on_human wins because the newest transition's target is not a hub node.
    facts = ChunkFacts(
        minted=True,
        leases=[LeaseFact(epoch=1, minted_at=_at(0))],
        routes_created=[RouteCreatedFact(created_at=_at(0))],
        transitions=[
            TransitionFact(to_node_id="nd_gate", to_node_executor=Executor.RUNNER, epoch=1, recorded_at=_at(1))
        ],
        decisions=[DecisionFact(decision_id="dec_1", submitted_at=_at(1), resolved=False)],
    )
    assert facts.status() is ChunkStatus.WAITING_ON_HUMAN


def test_requeue_supersedes_an_open_escalation() -> None:
    escalated = ChunkFacts(
        minted=True,
        leases=[LeaseFact(epoch=1, minted_at=_at(0))],
        routes_created=[RouteCreatedFact(created_at=_at(0))],
        escalations=[EscalationFact(epoch=1, recorded_at=_at(2))],
    )
    assert escalated.status() is ChunkStatus.NEEDS_HUMAN
    assert escalated.open_escalation() is not None

    # A later requeue fact closes the escalation by supersession (never a resolution)
    # and releases the route, so the chunk re-derives ready for a fresh FILL.
    requeued = ChunkFacts(
        minted=True,
        promoted=True,  # it was claimed, so it was promoted — requeue re-derives ready
        leases=[LeaseFact(epoch=1, minted_at=_at(0))],
        routes_created=[RouteCreatedFact(created_at=_at(0))],
        routes_released=[RouteReleasedFact(released_at=_at(3))],
        escalations=[EscalationFact(epoch=1, recorded_at=_at(2))],
        requeues=[RequeueFact(requeued_at=_at(3))],
    )
    assert requeued.open_escalation() is None
    assert requeued.status() is ChunkStatus.READY


def test_requeue_before_escalation_does_not_supersede() -> None:
    # A requeue that predates the escalation is not its supersession.
    facts = ChunkFacts(
        minted=True,
        leases=[LeaseFact(epoch=1, minted_at=_at(0))],
        escalations=[EscalationFact(epoch=1, recorded_at=_at(3))],
        requeues=[RequeueFact(requeued_at=_at(1))],
    )
    assert facts.open_escalation() is not None
    assert facts.status() is ChunkStatus.NEEDS_HUMAN


def test_running_base_has_no_gate() -> None:
    assert _running_facts().open_decision() is None
    assert _running_facts().status() is ChunkStatus.RUNNING
