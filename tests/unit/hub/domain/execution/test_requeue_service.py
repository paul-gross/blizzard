"""Requeue decides and writes in one locked unit."""

from dataclasses import replace
from typing import Any, cast
from unittest.mock import Mock

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import EscalationFact
from blizzard.hub.domain.execution.decisions import NotEscalated, RequeueService
from tests.support.completion_facts import AT_BUILD, CHUNK, T1
from tests.unit.hub.domain.execution.test_apply_service import FakeTransactions


@pytest.mark.parametrize("routed", [True, False])
def test_requeue_uses_locked_token_and_releases_only_a_present_route(routed: bool) -> None:
    clock = FixedClock(T1)
    transactions = FakeTransactions(clock)
    facts = Mock()
    facts.load_facts.return_value = replace(AT_BUILD, escalations=[EscalationFact(epoch=1, recorded_at=T1)])
    movement = Mock()
    movement.record_requeue.return_value = 42
    route = Mock()
    route.route_of.return_value = object() if routed else None
    service = RequeueService(
        facts=cast(Any, facts),
        transactions=cast(Any, transactions),
        movement=cast(Any, movement),
        route=cast(Any, route),
        clock=clock,
    )

    assert service.requeue(CHUNK, by="person", note="hello") == 42
    assert transactions.locked_ids == [[CHUNK.chunk_id]]
    facts.load_facts.assert_called_once_with(CHUNK.chunk_id, transaction=transactions)
    route.route_of.assert_called_once_with(CHUNK.chunk_id, transaction=transactions)
    assert movement.record_requeue.call_args.kwargs["transaction"] is transactions
    if routed:
        assert route.record_route_released.call_args.kwargs["transaction"] is transactions
    else:
        route.record_route_released.assert_not_called()


@pytest.mark.parametrize("missing", [True, False])
def test_requeue_refuses_without_writes(missing: bool) -> None:
    clock = FixedClock(T1)
    transactions = FakeTransactions(clock)
    facts = Mock()
    facts.load_facts.return_value = None if missing else AT_BUILD
    movement, route = Mock(), Mock()
    service = RequeueService(
        facts=cast(Any, facts),
        transactions=cast(Any, transactions),
        movement=cast(Any, movement),
        route=cast(Any, route),
        clock=clock,
    )
    with pytest.raises(ChunkNotFound if missing else NotEscalated):
        service.requeue(CHUNK, by="person")
    movement.record_requeue.assert_not_called()
    route.route_of.assert_not_called()
