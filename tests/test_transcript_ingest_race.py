"""Competing transcript ingests decide as a serial run would (component tier).

Each test lets one ingest take a guard read, then runs a competing ingest on a second thread
from inside that read and joins it within a short bound. An ingest that took the read before
its write unit opened lets the competitor finish in that window and then acts on the stale
read; one that re-reads inside its unit holds the competitor off until it commits. The unit's
keyed locks on the runner's lane and each chunk are the exclusion the ingest declares, pinned by
``tests/component/hub/domain/observability/test_transcript_ingest_service.py``; what serializes two ingests today, on SQLite and
Postgres alike, is the tenant-row lock every write unit opens with."""

from __future__ import annotations

import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from blizzard.hub.domain.observability.transcripts import (
    TranscriptCaps,
    TranscriptIngestResult,
    TranscriptIngestService,
    TranscriptSlice,
)
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry, RetiredRunnerGuard
from blizzard.hub.store.internal import transcript_segment_store
from blizzard.hub.store.internal.transcript_segment_store import TranscriptSegmentStore
from blizzard.hub.store.transactions import HubTransactions
from tests.support import HubHarness, build_hub, hub_store_connections, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_T0 = datetime(2026, 8, 9, tzinfo=UTC)

#: How long the hooked read waits on the competitor: long enough for an unfenced competitor to
#: finish, short of SQLite's busy timeout, so a fenced one is still waiting when the read returns.
_COMPETITOR_WINDOW_SECONDS = 0.5


class _NoRunners:
    def get_runner(self, runner_id: str) -> None:
        return None

    def is_retired(self, runner_id: str) -> bool:
        return False


_NO_RETIREMENTS = RetiredRunnerGuard(registry=cast(IReadRunnerRegistry, _NoRunners()))


def _store(hub: HubHarness) -> TranscriptSegmentStore:
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_T0)
    return TranscriptSegmentStore(hub_store_connections(hub.engine))


def _service(
    hub: HubHarness, store: TranscriptSegmentStore, caps: TranscriptCaps | None = None
) -> TranscriptIngestService:
    return TranscriptIngestService(
        store=store,
        transactions=HubTransactions(hub_store_connections(hub.engine)),
        retired=_NO_RETIREMENTS,
        clock=hub.clock,
        caps=caps,
    )


def _record(
    seq: int, *, turn_range_start: int, segment_id: str = "sg_1", runner_id: str = "r1", epoch: int = 1, turns: str
) -> tuple[int, TranscriptSlice]:
    return seq, TranscriptSlice(
        segment_id=segment_id,
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=epoch,
        spawn_generation=1,
        runner_id=runner_id,
        turn_range_start=turn_range_start,
        turn_range_end=turn_range_start,
        final=False,
        normalizer_version="v1",
        harness_version="claude-code-1.0",
        record_truncated=False,
        turns_json=turns,
    )


class _Competitor:
    """An ingest run on its own thread; :meth:`result` joins it and re-raises what it raised."""

    def __init__(self, ingest: Callable[[], TranscriptIngestResult]) -> None:
        self._ingest = ingest
        self._result: TranscriptIngestResult | None = None
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run)

    def _run(self) -> None:
        try:
            self._result = self._ingest()
        except BaseException as exc:
            self._error = exc

    def start(self) -> None:
        self._thread.start()
        self._thread.join(timeout=_COMPETITOR_WINDOW_SECONDS)

    def result(self) -> TranscriptIngestResult:
        self._thread.join(timeout=10)
        assert not self._thread.is_alive(), "the competing ingest never finished"
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


@contextmanager
def _competing_on(
    monkeypatch: pytest.MonkeyPatch, store: TranscriptSegmentStore, read: str, competitor: _Competitor
) -> Generator[None]:
    """Start ``competitor`` from inside this thread's first ``store.<read>``, once the read has its value."""
    real = getattr(store, read)
    caller = threading.get_ident()
    started = threading.Event()

    def hooked(*args: object, **kwargs: object) -> object:
        value = real(*args, **kwargs)
        if threading.get_ident() == caller and not started.is_set():
            started.set()
            competitor.start()
        return value

    monkeypatch.setattr(store, read, hooked)
    yield
    assert started.is_set(), f"the ingest never read {read}"


def test_competing_ingests_into_one_chunk_store_no_more_than_its_budget_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    store = _store(hub)
    service = _service(hub, store, TranscriptCaps(chunk_budget_max_bytes=50))
    tail = [_record(1, turn_range_start=0, segment_id="sg_a", runner_id="r1", epoch=3, turns="a" * 40)]
    head = [_record(1, turn_range_start=0, segment_id="sg_b", runner_id="r2", epoch=4, turns="b" * 40)]
    competitor = _Competitor(lambda: service.ingest("r2", head))

    with _competing_on(monkeypatch, store, "chunk_stored_bytes", competitor):
        first = service.ingest("r1", tail)
    second = competitor.result()

    assert sorted([(first.applied, first.capped), (second.applied, second.capped)]) == [([], [1]), ([1], [])]
    assert store.chunk_stored_bytes("ch_1") <= 50


def test_competing_ingests_of_one_record_both_ack_and_store_it_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    store = _store(hub)
    service = _service(hub, store)
    batch = [_record(1, turn_range_start=0, turns="[]")]
    competitor = _Competitor(lambda: service.ingest("r1", batch))

    with _competing_on(monkeypatch, store, "natural_key_state", competitor):
        first = service.ingest("r1", batch)
    second = competitor.result()

    assert isinstance(first, TranscriptIngestResult)
    assert sorted([first.applied + first.already_applied, second.applied + second.already_applied]) == [[1], [1]]
    assert len(store.records_for_segment("ch_1", "sg_1")) == 1


def test_competing_re_adjudications_of_one_rejected_record_both_report_it_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record fits the chunk's budget once: whichever re-adjudication lands it, the other
    must not count its bytes a second time and report the stored record ``capped``."""
    hub = build_hub(tmp_path)
    store = _store(hub)
    record = _record(1, turn_range_start=0, turns="a" * 40)
    rejected = _service(hub, store, TranscriptCaps(record_max_bytes=10)).ingest("r1", [record])
    assert rejected.capped == [1]
    service = _service(hub, store, TranscriptCaps(chunk_budget_max_bytes=50))
    re_offer = [(2, record[1])]
    competitor = _Competitor(lambda: service.ingest("r1", re_offer))

    with _competing_on(monkeypatch, store, "natural_key_state", competitor):
        first = service.ingest("r1", re_offer)
    second = competitor.result()

    assert (first.capped, second.capped) == ([], [])
    assert sorted([first.applied + first.already_applied, second.applied + second.already_applied]) == [[2], [2]]
    [content] = store.records_for_segment("ch_1", "sg_1")
    assert content.rejected is False


def test_an_ingest_computing_a_lower_mark_never_moves_a_committed_mark_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A push of seqs 1..10 that timed out client-side is still running when the next tick's
    push of 1..12 arrives."""
    hub = build_hub(tmp_path)
    store = _store(hub)
    service = _service(hub, store)
    resent = [_record(seq, turn_range_start=seq - 1, turns="[]") for seq in range(1, 13)]
    competitor = _Competitor(lambda: service.ingest("r1", resent))

    with _competing_on(monkeypatch, store, "high_water", competitor):
        service.ingest("r1", resent[:10])
    assert competitor.result().high_water == 12

    assert store.high_water("r1") == 12


def test_a_failing_later_write_commits_none_of_its_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = build_hub(tmp_path)
    store = _store(hub)
    service = _service(hub, store)
    real = transcript_segment_store.insert_accepted_stmt

    def failing_on_the_second(stmts: Any, record: TranscriptSlice, **kwargs: Any) -> Any:
        if record.turn_range_start == 1:
            raise RuntimeError("injected write failure")
        return real(stmts, record, **kwargs)

    monkeypatch.setattr(transcript_segment_store, "insert_accepted_stmt", failing_on_the_second)
    with pytest.raises(RuntimeError, match="injected write failure"):
        service.ingest("r1", [_record(1, turn_range_start=0, turns="[]"), _record(2, turn_range_start=1, turns="[]")])

    assert store.natural_key_state("sg_1", 0) == "absent"
    assert store.high_water("r1") == 0
