"""The operator requeue rule, pinned by value: which chunks it clears, which it refuses, and
in what order the refusals apply."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.runner.leases.escalations import ParkedEscalation
from blizzard.runner.lifecycle.takeover import OpenTakeover
from blizzard.runner.operator.requeue import (
    REQUEUE_REFUSALS,
    ChunkNotRequeueable,
    RequeueBlockedByOpenTakeover,
    RequeueMark,
    RequeueScope,
    RequeueStanding,
    requeue_mark,
)

pytestmark = pytest.mark.unit

_AT = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
_TAKEOVER = OpenTakeover(
    takeover_id="tko_1",
    chunk_id="ch_1",
    lease_id="lease_1",
    session_id="sess-a",
    workdir="/ws/e1",
    fence_epoch=None,
    opened_at=_AT,
)
_ESCALATION = ParkedEscalation(
    lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", epoch=1, session_id="sess-a", closed_at=_AT
)


def _scope(
    *,
    takeover: OpenTakeover | None = None,
    escalation: ParkedEscalation | None = _ESCALATION,
    held: tuple[str, ...] = ("e1",),
) -> RequeueScope:
    return RequeueScope(chunk_id="ch_1", open_takeover=takeover, open_escalation=escalation, held_environment_ids=held)


def test_an_escalated_chunk_holding_an_environment_gets_its_mark() -> None:
    assert requeue_mark(_scope(), at=_AT) == RequeueMark(chunk_id="ch_1", at=_AT)


def test_a_requeue_over_one_already_pending_is_marked_again() -> None:
    """Legal and idempotent: the scope carries no pending-requeue fact for the rule to read,
    so a second requeue yields the same mark, and one fresh attempt consumes both."""
    assert requeue_mark(_scope(), at=_AT) == requeue_mark(_scope(), at=_AT)


def test_an_open_takeover_refuses() -> None:
    with pytest.raises(RequeueBlockedByOpenTakeover, match="end the interactive session"):
        requeue_mark(_scope(takeover=_TAKEOVER), at=_AT)


def test_no_open_escalation_refuses() -> None:
    with pytest.raises(ChunkNotRequeueable, match="is not needs_human"):
        requeue_mark(_scope(escalation=None), at=_AT)


def test_no_held_environment_refuses_toward_the_hub() -> None:
    with pytest.raises(ChunkNotRequeueable, match="holds no environment on this runner — requeue it at the hub"):
        requeue_mark(_scope(held=()), at=_AT)


def test_the_takeover_refusal_wins_over_every_other() -> None:
    with pytest.raises(RequeueBlockedByOpenTakeover):
        requeue_mark(_scope(takeover=_TAKEOVER, escalation=None, held=()), at=_AT)


def test_not_needs_human_wins_over_unheld() -> None:
    assert _scope(escalation=None, held=()).standing is RequeueStanding.NOT_NEEDS_HUMAN


def test_the_verb_is_legal_only_from_requeueable() -> None:
    assert {standing for standing, refusal in REQUEUE_REFUSALS.items() if refusal is None} == {
        RequeueStanding.REQUEUEABLE
    }
    assert set(REQUEUE_REFUSALS) == set(RequeueStanding)
