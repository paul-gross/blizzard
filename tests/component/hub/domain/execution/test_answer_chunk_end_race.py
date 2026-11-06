"""An answer decides inside its own write (component tier, SQLite).

``POST /questions/{id}/answers`` loads the question and its chunk's facts before the answer's
write opens. Something committing at that point — a stop, a terminal completion, a competing
answer — must be decided against as if it had committed first: an ended chunk refuses the answer
as ``QuestionClosed``, landing no answer row and publishing no ``question-answered`` event, and a
racing answer still loses to the first one written. The decision's reads and its write all run
on the one token the unit's chunk lock returned."""

from __future__ import annotations

import json
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa

from blizzard.foundation.hub_event_types import HubEventType
from blizzard.hub.domain.chunk.model import QuestionClosed
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.questions import IWriteChunkQuestionsRepository
from blizzard.hub.domain.execution.questions import QuestionService
from blizzard.hub.domain.kernel.transactions import IHubTransactions, LockedTransaction, Transaction
from blizzard.hub.store import schema as s
from blizzard.hub.store.transactions import HubTransactions
from tests.support.chunk_questions import ask_question
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    chunk_stores,
    emitted_events,
    hub_scoped,
    hub_store_connections,
)


def _stop(hub: HubHarness, chunk_id: str) -> None:
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    hub.services.stop.stop(chunk, by="operator")


def _complete(hub: HubHarness, chunk_id: str) -> None:
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    hub.services.complete.complete(chunk, by="operator")


def _answer_rows(hub: HubHarness, question_id: str) -> int:
    answers = s.question_answers
    with hub.engine.begin() as conn:
        return conn.execute(
            hub_scoped(sa.select(sa.func.count()).select_from(answers).where(answers.c.question_id == question_id))
        ).scalar_one()


def _interleave(monkeypatch: pytest.MonkeyPatch, commit: Callable[[], None]) -> list[BaseException]:
    """Commit ``commit`` once the route has read the question and its chunk's facts and handed
    them to the answer, before the answer writes. Returns what the answer raised, if anything."""
    original: Callable[..., Any] = QuestionService.answer
    raised: list[BaseException] = []
    committed: list[bool] = []

    def interleave(self: QuestionService, *args: Any, **kwargs: Any) -> Any:
        if not committed:
            committed.append(True)
            commit()
        try:
            return original(self, *args, **kwargs)
        except Exception as exc:
            raised.append(exc)
            raise

    monkeypatch.setattr(QuestionService, "answer", interleave)
    return raised


@pytest.mark.parametrize("end", [_stop, _complete], ids=["stopped", "done"])
def test_an_answer_refuses_when_the_chunk_ends_after_the_route_read_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, end: Callable[[HubHarness, str], None]
) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ask_question(hub)
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "waiting_on_human"
    raised = _interleave(monkeypatch, lambda: end(hub, chunk_id))
    before = int(emitted_events(hub)[-1]["id"])

    resp = hub.client.post("/api/questions/qn_1/answers", json={"answer": "rest"})

    assert [type(exc) for exc in raised] == [QuestionClosed]
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == str(raised[0])
    assert _answer_rows(hub, "qn_1") == 0
    emitted = emitted_events(hub, since=before)
    assert HubEventType.QUESTION_ANSWERED not in [e["event"] for e in emitted]
    assert "question-answered" not in [
        json.loads(e["data"]).get("cause") for e in emitted if e["event"] == HubEventType.CHUNK_CHANGED
    ]
    poll = hub.client.get("/api/fleet/questions/qn_1").json()
    assert (poll["answered"], poll["answer"]) == (False, None)


def test_a_racing_second_answer_loses_to_the_first_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = build_hub(tmp_path)
    ask_question(hub)
    first: list[Any] = []

    def answer_first() -> None:
        first.append(hub.client.post("/api/questions/qn_1/answers", json={"answer": "graphql"}))

    _interleave(monkeypatch, answer_first)

    second = hub.client.post("/api/questions/qn_1/answers", json={"answer": "rest"})

    assert first[0].status_code == 201, first[0].text
    assert second.status_code == 409, second.text
    assert (second.json()["won"], second.json()["answer"]) == (False, "graphql")
    assert _answer_rows(hub, "qn_1") == 1


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
    def __init__(self, unit: Transaction, locked: list[LockedTransaction]) -> None:
        self._unit = unit
        self._locked = locked

    def lock_chunks(self, chunk_ids: Sequence[str]) -> LockedTransaction:
        locked = self._unit.lock_chunks(chunk_ids)
        self._locked.append(locked)
        return locked


class _RecordingTransactions:
    """The hub's units, recording the token each unit's ``lock_chunks`` returns."""

    def __init__(self, inner: IHubTransactions) -> None:
        self._inner = inner
        self.locked: list[LockedTransaction] = []

    @contextmanager
    def begin(self) -> Generator[_RecordingUnit]:
        with self._inner.begin() as unit:
            yield _RecordingUnit(unit, self.locked)


def test_an_answer_reads_and_writes_on_the_token_its_chunk_lock_returned(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ask_question(hub)
    stores = chunk_stores(hub.engine, hub.clock)
    questions, facts = _Recording(stores.questions), _Recording(stores.facts)
    transactions = _RecordingTransactions(HubTransactions(hub_store_connections(hub.engine)))
    service = QuestionService(
        questions=cast(IWriteChunkQuestionsRepository, questions),
        facts=cast(IReadChunkFactsRepository, facts),
        transactions=cast(IHubTransactions, transactions),
        clock=hub.clock,
    )
    question = stores.questions.get_question("qn_1")
    assert question is not None

    outcome = service.answer(question, answer="rest", answered_by="operator")

    assert outcome.won
    [token] = transactions.locked
    assert questions.calls == [("get_question", token), ("answer_question", token)]
    assert facts.calls == [("load_facts", token)]
