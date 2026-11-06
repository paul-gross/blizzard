"""A gate resolution decides inside its own write (component tier, SQLite).

``POST /decisions/{id}/resolutions`` loads the decision at the edge, and the resolve reads its
chunk's status before the resolution's write opens. Something committing at that point — a stop,
a restart onto the gate's node, a competing resolution — must be decided against as if it had
committed first: a closed gate refuses the resolution as ``DecisionClosed``, landing no
resolution row and publishing no ``decision-resolved`` event, and a racing resolution still loses
to the first one written. The decision's reads and its write all run on the one token the unit's
chunk lock returned."""

from __future__ import annotations

import json
from collections.abc import Callable, Generator, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa

from blizzard.foundation.hub_event_types import HubEventType
from blizzard.hub.domain.chunk.model import DecisionClosed
from blizzard.hub.domain.chunk.ports.decisions import IWriteChunkDecisionsRepository
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.execution.decisions import DecisionService
from blizzard.hub.domain.kernel.transactions import IHubTransactions, LockedTransaction, Transaction
from blizzard.hub.domain.runners.registration import RetiredRunnerGuard
from blizzard.hub.store import schema as s
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.store.transactions import HubTransactions
from tests.support.gate_decisions import (
    _open_decision,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.gate_decisions
)
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    chunk_stores,
    emitted_events,
    hub_scoped,
    hub_store_connections,
)


def _chunk_of(hub: HubHarness, decision_id: str) -> str:
    decision = hub.services.chunks.decisions.get_decision(decision_id)
    assert decision is not None
    return decision.chunk_id


def _stop(hub: HubHarness, chunk_id: str) -> None:
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    hub.services.stop.stop(chunk, by="operator")


def _restart_onto_gate(hub: HubHarness, chunk_id: str) -> None:
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    graph = hub.services.graphs.get(chunk.graph_id)
    assert graph is not None
    hub.services.restart.restart(chunk, graph, node_name="approve-gate", by="operator")


def _resolution_rows(hub: HubHarness, decision_id: str) -> int:
    resolutions = s.decision_resolutions
    with hub.engine.begin() as conn:
        return conn.execute(
            hub_scoped(
                sa.select(sa.func.count()).select_from(resolutions).where(resolutions.c.decision_id == decision_id)
            )
        ).scalar_one()


def _interleave(monkeypatch: pytest.MonkeyPatch, commit: Callable[[], None]) -> list[BaseException]:
    """Commit ``commit`` once the resolve has made every read it takes before its write, as the
    first write it opens is about to open. Returns what the resolve raised, if anything."""
    original_resolve: Callable[..., Any] = DecisionService.resolve
    original_write: Callable[..., AbstractContextManager[Any]] = ScopedConnections.write
    raised: list[BaseException] = []
    state = {"armed": False, "fired": False}

    def write(self: ScopedConnections, operation: str, **kwargs: Any) -> AbstractContextManager[Any]:
        if state["armed"]:
            state["armed"], state["fired"] = False, True
            commit()
        return original_write(self, operation, **kwargs)

    def resolve(self: DecisionService, *args: Any, **kwargs: Any) -> Any:
        state["armed"] = not state["fired"]
        try:
            return original_resolve(self, *args, **kwargs)
        except Exception as exc:
            raised.append(exc)
            raise
        finally:
            state["armed"] = False

    monkeypatch.setattr(ScopedConnections, "write", write)
    monkeypatch.setattr(DecisionService, "resolve", resolve)
    return raised


@pytest.mark.parametrize("close", [_stop, _restart_onto_gate], ids=["stopped", "restarted"])
def test_a_resolution_refuses_when_the_gate_closes_after_the_resolve_read_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, close: Callable[[HubHarness, str], None]
) -> None:
    hub = build_hub(tmp_path)
    decision_id = _open_decision(hub)
    chunk_id = _chunk_of(hub, decision_id)
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "waiting_on_human"
    raised = _interleave(monkeypatch, lambda: close(hub, chunk_id))
    before = int(emitted_events(hub)[-1]["id"])

    resp = hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "approve"})

    assert [type(exc) for exc in raised] == [DecisionClosed]
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"detail": str(raised[0])}
    assert _resolution_rows(hub, decision_id) == 0
    emitted = emitted_events(hub, since=before)
    assert HubEventType.DECISION_RESOLVED not in [e["event"] for e in emitted]
    assert "decision-resolved" not in [
        json.loads(e["data"]).get("cause") for e in emitted if e["event"] == HubEventType.CHUNK_CHANGED
    ]


def test_a_racing_second_resolution_loses_to_the_first_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = build_hub(tmp_path)
    decision_id = _open_decision(hub)
    first: list[Any] = []

    def resolve_first() -> None:
        first.append(hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "approve"}))

    _interleave(monkeypatch, resolve_first)

    second = hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "reject"})

    assert first[0].status_code == 200, first[0].text
    assert second.status_code == 409, second.text
    assert (second.json()["already_resolved_by"], second.json()["resolved_choice"]) == ("operator", "approve")
    assert _resolution_rows(hub, decision_id) == 1


class _Recording:
    """Forwards every call to ``inner``, recording each method name with the ``transaction``
    it was handed."""

    def __init__(self, inner: object) -> None:
        self.inner = inner
        self.calls: list[tuple[str, object]] = []

    def __getattr__(self, name: str) -> Any:
        method = getattr(self.inner, name)

        def record(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, kwargs.get("transaction")))
            return method(*args, **kwargs)

        return record


class _RecordingUnit:
    def __init__(self, unit: Transaction, locked: list[tuple[Sequence[str], LockedTransaction]]) -> None:
        self._unit = unit
        self._locked = locked

    def lock_chunks(self, chunk_ids: Sequence[str]) -> LockedTransaction:
        locked = self._unit.lock_chunks(chunk_ids)
        self._locked.append((list(chunk_ids), locked))
        return locked


class _RecordingTransactions:
    """The hub's units, recording each unit's ``lock_chunks`` set and the token it returns."""

    def __init__(self, inner: IHubTransactions) -> None:
        self._inner = inner
        self.locked: list[tuple[Sequence[str], LockedTransaction]] = []

    @contextmanager
    def begin(self) -> Generator[_RecordingUnit]:
        with self._inner.begin() as unit:
            yield _RecordingUnit(unit, self.locked)


def test_a_resolution_reads_and_writes_on_the_token_its_chunk_lock_returned(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    decision_id = _open_decision(hub)
    stores = chunk_stores(hub.engine, hub.clock)
    decisions, facts = _Recording(stores.decisions), _Recording(stores.facts)
    transactions = _RecordingTransactions(HubTransactions(hub_store_connections(hub.engine)))
    service = DecisionService(
        facts=cast(IReadChunkFactsRepository, facts),
        route=stores.route,
        decisions=cast(IWriteChunkDecisionsRepository, decisions),
        exclusive=stores.exclusive,
        transactions=cast(IHubTransactions, transactions),
        retired=RetiredRunnerGuard(registry=cast(Any, None)),
        clock=hub.clock,
        closer_available=lambda _source: False,
    )
    decision = stores.decisions.get_decision(decision_id)
    assert decision is not None

    result = service.resolve(decision, choice="approve", resolved_by="operator")

    assert result.resolved
    [(chunk_ids, token)] = transactions.locked
    assert chunk_ids == [decision.chunk_id]
    assert decisions.calls == [("get_decision", token), ("record_decision_resolution", token)]
    assert facts.calls == [("load_facts", token)]
