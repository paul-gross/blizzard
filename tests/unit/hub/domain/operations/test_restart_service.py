from __future__ import annotations

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts
from blizzard.hub.domain.chunk.ports.decisions import IWriteChunkDecisionsRepository
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.flow_submissions import IReadChunkFlowSubmissions
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.graph.model import Graph, IReadGraphRepository
from blizzard.hub.domain.kernel.transactions import IHubTransactions, Transaction
from blizzard.hub.domain.operations.restart import RestartService

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_1", work_refs=[], minted_at=_T0)


class _Untouchable:
    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"RestartService should not touch {name!r}")


_TOKEN = cast(Transaction, object())


class _GoneFacts(_Untouchable):
    def load_facts(self, chunk_id: str, *, transaction: Transaction | None = None) -> ChunkFacts | None:
        assert chunk_id == _CHUNK.chunk_id
        assert transaction is _TOKEN
        return None


class _FakeTransactions:
    @contextmanager
    def begin(self) -> Generator[Any]:
        yield self

    def lock_chunks(self, chunk_ids: Sequence[str]) -> Transaction:
        assert chunk_ids == [_CHUNK.chunk_id]
        return _TOKEN


def test_restart_refuses_a_chunk_gone_under_the_lock_and_writes_nothing() -> None:
    service = RestartService(
        movement=cast(IWriteChunkMovementRepository, _Untouchable()),
        submissions=cast(IReadChunkFlowSubmissions, _Untouchable()),
        decisions=cast(IWriteChunkDecisionsRepository, _Untouchable()),
        facts=cast(IReadChunkFactsRepository, _GoneFacts()),
        graphs=cast(IReadGraphRepository, _Untouchable()),
        clock=FixedClock(_T0),
        transactions=cast(IHubTransactions, _FakeTransactions()),
        record=cast(IReadChunkRecordRepository, _Untouchable()),
        closer_available=lambda source: source == "hub",
        events=cast(EventLogService, _Untouchable()),
    )

    with pytest.raises(ChunkNotFound):
        service.restart(_CHUNK, cast(Graph, _Untouchable()), node_name=None, by="operator")


def test_restart_keeps_reads_writes_and_gate_in_one_locked_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import Mock

    from blizzard.hub.domain.chunk.flow_writes import LeverWrite, OpenSubmission
    from blizzard.hub.domain.operations import restart as restart_module
    from tests.support.scaffolding import make_graph

    facts = Mock()
    facts.load_facts.return_value = ChunkFacts(minted=True)
    record = Mock()
    record.get.return_value = _CHUNK
    graphs = Mock()
    graphs.is_retired.return_value = False
    submissions = Mock()
    submissions.open_flow_submissions.return_value = (OpenSubmission("sub_1", False),)
    movement = Mock()
    movement.record_restart.return_value = LeverWrite(fact_id=42)
    events = Mock()
    gate = Mock(return_value=())
    monkeypatch.setattr(restart_module, "open_graph_gate_on_arrival", gate)
    graph = make_graph("gr_1", "one", entry_node_id="nd_1")
    target = make_graph("gr_2", "two", entry_node_id="nd_2")
    # Name the target node explicitly, so the two graphs need not share a node name.
    node = Mock(node_id="nd_2")
    monkeypatch.setattr(restart_module, "restart_landing", Mock(return_value=node))
    service = RestartService(
        movement=movement,
        submissions=submissions,
        decisions=Mock(),
        facts=facts,
        record=record,
        graphs=graphs,
        clock=FixedClock(_T0),
        transactions=cast(IHubTransactions, _FakeTransactions()),
        closer_available=lambda _: False,
        events=events,
    )
    result = service.restart(_CHUNK, graph, node_name="next", by="operator", to_graph=target)
    assert result.fact_id == 42
    assert facts.load_facts.call_count == 2
    for call in facts.load_facts.call_args_list:
        assert call.kwargs["transaction"] is _TOKEN
    record.get.assert_called_once_with(_CHUNK.chunk_id, transaction=_TOKEN)
    graphs.is_retired.assert_called_once_with(target.graph_id, transaction=_TOKEN)
    submissions.open_flow_submissions.assert_called_once_with(_CHUNK.chunk_id, transaction=_TOKEN)
    assert movement.record_restart.call_args.kwargs["transaction"] is _TOKEN
    assert movement.record_restart.call_args.kwargs["waiting"] == ("sub_1",)
    assert gate.call_args.args[2] is _TOKEN
    events.publish.assert_called_once_with()
