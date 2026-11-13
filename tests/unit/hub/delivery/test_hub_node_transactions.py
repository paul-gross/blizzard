"""The hub router joins its reads, writes and turn end to the same locked unit."""

from typing import cast
from unittest.mock import MagicMock

from blizzard.hub.delivery.hub_node import HubNodeRouter
from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.graph.model import Node
from tests.support.fake_transactions import FakeTransactions


def _router(
    transactions: FakeTransactions, facts: MagicMock, artifacts: MagicMock, hub_exec: MagicMock
) -> HubNodeRouter:
    return HubNodeRouter(
        artifacts=artifacts,
        hub_exec=hub_exec,
        escalations=MagicMock(),
        record=MagicMock(),
        transactions=transactions,
        events=MagicMock(),
        clock=MagicMock(),
        repositories=MagicMock(),
        forge_records=MagicMock(),
        links=MagicMock(),
        targets=MagicMock(),
        projects=MagicMock(),
        work_sources=MagicMock(),
        movement=MagicMock(),
        decisions=MagicMock(),
        graphs=MagicMock(),
        facts=facts,
        follow_latest_default=False,
    )


def test_wait_and_turn_end_join_the_chunk_lock() -> None:
    transactions = FakeTransactions()
    hub_exec = MagicMock()
    ending = MagicMock()
    router = _router(transactions, MagicMock(), MagicMock(), hub_exec)
    chunk = MagicMock(spec=Chunk, chunk_id="chunk")
    node = MagicMock(spec=Node, node_id="node")

    router.record_wait(chunk, node, epoch=3, kind="pending", ending=ending)

    token = hub_exec.record_hub_node_poll.call_args.kwargs["transaction"]
    assert transactions.locked_chunks == [["chunk"]]
    assert ending.end.call_args.args[0] is token


def test_standing_visit_reads_facts_and_artifacts_on_the_same_token() -> None:
    transactions = FakeTransactions()
    facts = MagicMock()
    artifacts = MagicMock()
    router = _router(transactions, facts, artifacts, MagicMock())
    chunk = MagicMock(spec=Chunk, chunk_id="chunk")
    node = MagicMock(spec=Node, node_id="node")
    facts.load_facts.return_value.delivering_at.return_value = True
    artifacts.load_artifacts.return_value = []

    with transactions.begin() as unit:
        token = unit.lock_set(chunks=["chunk"])
        visit = router._standing_visit(token, chunk, node, epoch=3)  # pyright: ignore[reportPrivateUsage]  # seam under test

    assert visit is not None
    facts.load_facts.assert_called_once_with("chunk", transaction=token)
    artifacts.load_artifacts.assert_called_once_with("chunk", transaction=token)


def test_bounce_rereads_facts_after_the_write_on_the_locked_token() -> None:
    transactions = FakeTransactions()
    facts = MagicMock()
    artifacts = MagicMock()
    router = _router(transactions, facts, artifacts, MagicMock())
    chunk = MagicMock(spec=Chunk, chunk_id="chunk")
    node = MagicMock(spec=Node, node_id="node", bounce_cap=2)
    calls = MagicMock()
    calls.attach_mock(facts, "facts")
    # Capture the escalation writer supplied to this router.
    escalations = cast(MagicMock, router._escalations)  # pyright: ignore[reportPrivateUsage]  # seam under test
    calls.attach_mock(escalations, "escalations")
    facts.load_facts.return_value.bounces_over_cap.return_value = False

    with transactions.begin() as unit:
        token = unit.lock_set(chunks=["chunk"])
        crossed = router._bounce(  # pyright: ignore[reportPrivateUsage]  # seam under test
            token, chunk, node, hub_epoch=4, cause="failure", envelope="envelope"
        )

    assert not crossed
    assert escalations.record_bounce.call_args.kwargs["transaction"] is token
    assert facts.load_facts.call_args.kwargs["transaction"] is token
    names = [call[0] for call in calls.mock_calls]
    assert names.index("escalations.record_bounce") < max(
        i for i, name in enumerate(names) if name == "facts.load_facts"
    )
