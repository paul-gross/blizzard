"""The close-intent outbox's drain. ``ChunkDeliveryStore.pending_close_intents()``/
``record_work_item_closure()`` are exercised against a real, migrated store. The enqueue side
(landing/completion) is covered by ``tests/test_close_intents_enqueue.py``; this file
covers the drain that retires what the enqueue queued."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.event_log import EVENT_LOG_SEVERITY, EventLogKind
from blizzard.foundation.work_items import WorkItemClosure
from blizzard.hub.domain.artifacts import ArtifactRow
from blizzard.hub.domain.chunks.artifacts import IReadChunkArtifactsRepository, IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunks.delivery import IWriteChunkDeliveryRepository
from blizzard.hub.domain.chunks.events import IWriteChunkEventsRepository
from blizzard.hub.domain.chunks.fence import EpochAdmission
from blizzard.hub.domain.chunks.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.delivery_read import DeliverySources, DeliveryTrace
from blizzard.hub.domain.event_log import EventLogService
from blizzard.hub.domain.graph import RESERVED_TERMINAL
from blizzard.hub.domain.work import PendingCloseIntent, WorkItemCloseOutcome, WorkRef
from blizzard.hub.domain.work_closure import (
    CLOSE_DRAIN_BACKOFF_BASE_SECONDS,
    CLOSE_DRAIN_PASS_LIMIT,
    CloseIntentDrainer,
    close_intent_is_due,
)
from blizzard.hub.events.broker import EVENT_LOGGED
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.work_sources.registry import WorkSourceRegistry
from tests.support import (
    FakeCloser,
    HubHarness,
    build_hub,
    count_queries,
    emitted_events,
    hub_store_connections,
    ingest,
)

pytestmark = pytest.mark.unit


def _event_log(hub: HubHarness) -> EventLogService:
    """A real :class:`EventLogService` over the harness's own store and broker, for a
    test that wires its own :class:`CloseIntentDrainer` rather than taking
    ``hub.services.close_drain``."""
    return EventLogService(events=cast(IWriteChunkEventsRepository, hub.services.chunks.events), publisher=hub.events)


def _event_logged_frames(hub: HubHarness, *, since: int = 0) -> list[dict]:
    return [json.loads(e["data"]) for e in emitted_events(hub, since=since) if e["event"] == EVENT_LOGGED]


def _land(hub: HubHarness, chunk_id: str, *, repo: str = "widget") -> None:
    """Simulate a generic hub command node's mid-run ``merged/<repo>`` marker —
    the current landing truth :func:`~blizzard.hub.domain.work.has_landed_repos` reads
    independent of any real graph/node machinery. Enqueues a pending close
    intent as a side effect of the same write."""
    cast(IWriteChunkArtifactsRepository, hub.services.chunks.artifacts).record_hub_artifact(
        chunk_id,
        node_id="nd_deliver",
        node_name="deliver",
        epoch=1,
        name=f"merged/{repo}",
        content="sha",
        at=hub.clock.now(),
        admission=EpochAdmission.AT_OR_ABOVE,
    )


# ChunkDeliveryStore.record_work_item_closure() — retires its matching intent in the same
# transaction whenever the outcome is closed/gone.


@pytest.mark.component
def test_record_work_item_closure_retires_the_matching_pending_intent(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)

    wrote = cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery).record_work_item_closure(
        chunk_id,
        pointer=WorkRef(source="default", ref="1"),
        outcome=WorkItemCloseOutcome.CLOSED,
        reason=None,
        at=hub.clock.now(),
    )

    assert wrote is True
    assert hub.services.chunks.delivery.pending_close_intents() == []


@pytest.mark.component
def test_record_work_item_closure_replay_still_retires_an_interrupted_intent(tmp_path: Path) -> None:
    """The crash-recovery case: the outcome was already recorded on a prior pass — the
    crash landed before retirement — and a replay finishes the retirement even though it
    writes no fresh outcome row."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    pointer = WorkRef(source="default", ref="1")
    cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery).record_work_item_closure(
        chunk_id, pointer=pointer, outcome=WorkItemCloseOutcome.CLOSED, reason=None, at=hub.clock.now()
    )

    wrote = cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery).record_work_item_closure(
        chunk_id, pointer=pointer, outcome=WorkItemCloseOutcome.CLOSED, reason=None, at=hub.clock.now()
    )

    assert wrote is False  # no fresh outcome row — this is a replay
    assert hub.services.chunks.delivery.pending_close_intents() == []  # retirement still finished


@pytest.mark.component
def test_record_work_item_closure_against_a_never_enqueued_ref_writes_no_intent_row(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    wrote = cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery).record_work_item_closure(
        "ch_nonexistent",
        pointer=WorkRef(source="default", ref="1"),
        outcome=WorkItemCloseOutcome.CLOSED,
        reason=None,
        at=hub.clock.now(),
    )

    assert wrote is True  # the outcome fact itself is unconditional
    assert hub.services.chunks.delivery.pending_close_intents() == []


@pytest.mark.component
def test_record_work_item_closure_failed_outcome_leaves_the_intent_pending(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    pointer = WorkRef(source="default", ref="1")

    cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery).record_work_item_closure(
        chunk_id, pointer=pointer, outcome=WorkItemCloseOutcome.FAILED, reason="boom", at=hub.clock.now()
    )

    # Not retired: still pending, carrying the one attempt it just ticked.
    [intent] = hub.services.chunks.delivery.pending_close_intents()
    assert intent == PendingCloseIntent(chunk_id=chunk_id, ref=pointer)
    assert intent.attempt_count == 1
    assert intent.last_attempt_at == hub.clock.now()


# The backoff history the read carries, and the rule that consumes it ------------------------------------


@pytest.mark.component
def test_pending_close_intents_carries_each_intents_attempt_history(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    delivery = cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery)
    [fresh] = delivery.pending_close_intents()
    assert (fresh.attempt_count, fresh.last_attempt_at) == (0, None)

    delivery.record_work_item_closure(
        chunk_id, pointer=fresh.ref, outcome=WorkItemCloseOutcome.FAILED, reason="x", at=hub.clock.now()
    )
    hub.clock.advance(timedelta(seconds=10))  # a backed-off intent is still returned
    delivery.record_close_attempt_skipped(fresh.intent_id, at=hub.clock.now())

    [backed_off] = delivery.pending_close_intents()
    assert backed_off.intent_id == fresh.intent_id
    assert backed_off.attempt_count == 2
    assert backed_off.last_attempt_at == hub.clock.now()


@pytest.mark.parametrize(
    ("attempt_count", "threshold_seconds"),
    [(1, 60), (2, 120), (3, 240), (4, 480), (5, 960), (6, 1920), (7, 3600), (8, 3600)],
)
def test_close_intent_is_due_exactly_at_its_exponential_threshold_capped_at_one_hour(
    attempt_count: int, threshold_seconds: int
) -> None:
    """base x 2^(n-1): 60s, 120s, 240s, ... capped at 3600s."""
    last = datetime(2026, 8, 1, tzinfo=UTC)

    def due(elapsed: int) -> bool:
        return close_intent_is_due(last + timedelta(seconds=elapsed), attempt_count=attempt_count, last_attempt_at=last)

    assert not due(threshold_seconds - 1)
    assert due(threshold_seconds)


@pytest.mark.parametrize(("attempt_count", "last_attempt_at"), [(0, None), (None, None), (3, None)])
def test_close_intent_is_due_with_no_prior_attempt(attempt_count: int | None, last_attempt_at: datetime | None) -> None:
    assert close_intent_is_due(
        datetime(2026, 8, 1, tzinfo=UTC), attempt_count=attempt_count, last_attempt_at=last_attempt_at
    )


def test_the_backoff_base_matches_the_sweep_interval() -> None:
    """The backoff's base second is meant to mirror the close-drain sweep's own tick;
    a drift between the two constants would silently change the
    backoff's real cadence relative to the sweep that drives it."""
    from blizzard.hub.app import CLOSE_DRAIN_INTERVAL_SECONDS

    assert CLOSE_DRAIN_BACKOFF_BASE_SECONDS == CLOSE_DRAIN_INTERVAL_SECONDS


@pytest.mark.component
def test_pending_close_intents_issues_a_flat_query_count_regardless_of_backlog_size(tmp_path: Path) -> None:
    """The due-check reads every intent's backoff history through one
    outer-joined, aggregated statement — never one query per intent."""
    hub = build_hub(tmp_path)
    delivery = cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery)
    for i in range(20):
        chunk_id = ingest(hub, [{"source": "default", "ref": str(i)}], promote=True)
        _land(hub, chunk_id)
        delivery.record_work_item_closure(
            chunk_id,
            pointer=WorkRef(source="default", ref=str(i)),
            outcome=WorkItemCloseOutcome.FAILED,
            reason="x",
            at=hub.clock.now(),
        )

    count = count_queries(hub.engine, delivery.pending_close_intents)
    assert count == 1


# CloseIntentDrainer.sweep() — fakes (unit tier)


@dataclass
class _RecordedEvent:
    severity: str
    kind: str
    chunk_id: str | None
    message: str
    detail: dict | None


class _FakeCloseChunks:
    """The minimal slice of :class:`IWriteChunkDeliveryRepository`/:class:`EventLogService`
    :class:`CloseIntentDrainer` calls. ``record_work_item_closure`` also retires the
    matching intent — recorded rather than persisted, matching the real store's own
    folded transaction."""

    def __init__(self, candidates: list[PendingCloseIntent]) -> None:
        self._candidates = candidates
        self.closures: list[tuple[str, WorkRef, WorkItemCloseOutcome, str | None]] = []
        self.retired: list[tuple[str, WorkRef]] = []
        self.events: list[_RecordedEvent] = []
        self.skipped_attempts: list[int] = []
        self._written: set[tuple[str, str, str, str]] = set()
        self.delivery_reads: list[list[str]] = []
        self.sources: dict[str, DeliverySources] = {}

    def delivery_sources_for(self, chunk_ids: list[str]) -> dict[str, DeliverySources]:
        self.delivery_reads.append(list(chunk_ids))
        return {c: self.sources[c] for c in chunk_ids if c in self.sources}

    def pending_close_intents(self) -> list[PendingCloseIntent]:
        return list(self._candidates)

    def record_close_attempt_skipped(self, intent_id: int, *, at: object) -> None:
        self.skipped_attempts.append(intent_id)

    def record_work_item_closure(
        self, chunk_id: str, *, pointer: WorkRef, outcome: WorkItemCloseOutcome, reason: str | None, at: object
    ) -> bool:
        if outcome in (WorkItemCloseOutcome.CLOSED, WorkItemCloseOutcome.GONE):
            self.retired.append((chunk_id, pointer))
        key = (chunk_id, pointer.source, pointer.ref, outcome.value)
        if key in self._written:
            return False
        self._written.add(key)
        self.closures.append((chunk_id, pointer, outcome, reason))
        return True

    def record(
        self,
        *,
        kind: EventLogKind,
        runner_id: str,
        chunk_id: str | None,
        lease_id: str | None,
        node_name: str | None,
        message: str,
        detail: dict | None,
        at: object,
    ) -> int:
        self.events.append(
            _RecordedEvent(
                severity=EVENT_LOG_SEVERITY[kind], kind=kind, chunk_id=chunk_id, message=message, detail=detail
            )
        )
        return len(self.events)


def _as_delivery(chunks: _FakeCloseChunks) -> IWriteChunkDeliveryRepository:
    return cast(IWriteChunkDeliveryRepository, chunks)


def _as_events(chunks: _FakeCloseChunks) -> EventLogService:
    return cast(EventLogService, chunks)


_NOW = datetime(2026, 8, 1, tzinfo=UTC)


def _drainer(
    chunks: _FakeCloseChunks,
    closers: dict[str, FakeCloser],
    clock: FixedClock | None = None,
    public_url: str | None = None,
) -> CloseIntentDrainer:
    registry = WorkSourceRegistry({}, closers=closers)  # type: ignore[arg-type]
    clock = clock or FixedClock(_NOW)
    return CloseIntentDrainer(
        delivery=_as_delivery(chunks),
        artifacts=cast(IReadChunkArtifactsRepository, chunks),
        events=_as_events(chunks),
        work_sources=registry,
        clock=clock,
        public_url=public_url,
    )


def test_sweep_closes_a_pending_intents_pointer_and_records_an_info_event() -> None:
    pointer = WorkRef(source="default", ref="1")
    closer = FakeCloser()
    chunks = _FakeCloseChunks([PendingCloseIntent(chunk_id="ch_1", ref=pointer)])

    _drainer(chunks, {"default": closer}).sweep()

    assert closer.closed == [pointer]
    assert chunks.closures == [("ch_1", pointer, WorkItemCloseOutcome.CLOSED, None)]
    assert chunks.retired == [("ch_1", pointer)]
    assert len(chunks.events) == 1
    assert chunks.events[0].severity == "info"
    assert chunks.events[0].kind == "work-item-closed"


def test_sweep_closes_each_intent_through_its_own_sources_binding() -> None:
    alpha_ref = WorkRef(source="alpha", ref="1")
    beta_ref = WorkRef(source="beta", ref="2")
    alpha_closer = FakeCloser()
    beta_closer = FakeCloser()
    chunks = _FakeCloseChunks(
        [PendingCloseIntent(chunk_id="ch_1", ref=alpha_ref), PendingCloseIntent(chunk_id="ch_2", ref=beta_ref)]
    )

    _drainer(chunks, {"alpha": alpha_closer, "beta": beta_closer}).sweep()

    assert alpha_closer.closed == [alpha_ref]
    assert beta_closer.closed == [beta_ref]


def test_sweep_continues_past_one_ref_that_raises() -> None:
    good = WorkRef(source="default", ref="1")
    bad = WorkRef(source="default", ref="2")
    closer = FakeCloser(fail_refs={"2"})
    chunks = _FakeCloseChunks(
        [PendingCloseIntent(chunk_id="ch_1", ref=good), PendingCloseIntent(chunk_id="ch_2", ref=bad)]
    )

    _drainer(chunks, {"default": closer}).sweep()  # must not raise

    assert closer.closed == [good]
    outcomes = {ref.ref: outcome for _cid, ref, outcome, _reason in chunks.closures}
    assert outcomes["1"] is WorkItemCloseOutcome.CLOSED
    assert outcomes["2"] is WorkItemCloseOutcome.FAILED
    assert {e.kind for e in chunks.events} == {"work-item-closed", "work-item-close-failed"}
    assert chunks.retired == [("ch_1", good)]  # the failed one stays pending — never retired


def test_sweep_records_a_gone_ref_distinctly_from_a_failed_one() -> None:
    ref = WorkRef(source="default", ref="1")
    closer = FakeCloser(gone_refs={"1"})
    chunks = _FakeCloseChunks([PendingCloseIntent(chunk_id="ch_1", ref=ref)])

    _drainer(chunks, {"default": closer}).sweep()

    assert chunks.closures == [("ch_1", ref, WorkItemCloseOutcome.GONE, "1 no longer exists")]
    assert chunks.retired == [("ch_1", ref)]  # gone retires the intent, same as closed
    assert chunks.events[0].severity == "warning"
    assert chunks.events[0].kind == "work-item-close-failed"


def test_sweep_leaves_a_failed_intent_pending_and_retries_it() -> None:
    ref = WorkRef(source="default", ref="1")
    closer = FakeCloser(fail_refs={"1"})
    chunks = _FakeCloseChunks([PendingCloseIntent(chunk_id="ch_1", ref=ref)])

    _drainer(chunks, {"default": closer}).sweep()

    assert chunks.closures == [("ch_1", ref, WorkItemCloseOutcome.FAILED, "boom closing 1")]
    assert chunks.retired == []


def test_sweep_skips_an_intent_whose_source_has_no_closer_bound() -> None:
    """An intent from a source not seated as a closer stays pending, untouched —
    the sweep neither closes it nor retires it, and issues no forge call. It still ticks
    the intent's own backoff clock, so a persistently source-less intent
    is not reconsidered on every sweep forever."""
    unopted_ref = WorkRef(source="unopted", ref="1")
    chunks = _FakeCloseChunks([PendingCloseIntent(chunk_id="ch_1", ref=unopted_ref, intent_id=7)])

    _drainer(chunks, {}).sweep()

    assert chunks.closures == []
    assert chunks.retired == []
    assert chunks.events == []
    assert chunks.skipped_attempts == [7]


def test_sweep_over_an_empty_queue_issues_no_forge_call() -> None:
    closer = FakeCloser()

    _drainer(_FakeCloseChunks([]), {"default": closer}).sweep()

    assert closer.closed == []


# CloseIntentDrainer.sweep() — real store + FakeCloser (component tier)


def test_sweep_does_not_attempt_a_backed_off_intent_until_its_threshold_elapses() -> None:
    pointer = WorkRef(source="default", ref="1")
    closer = FakeCloser()
    backed_off = PendingCloseIntent(chunk_id="ch_1", ref=pointer, attempt_count=1, last_attempt_at=_NOW)
    chunks = _FakeCloseChunks([backed_off])
    clock = FixedClock(_NOW + timedelta(seconds=59))
    drainer = _drainer(chunks, {"default": closer}, clock)

    drainer.sweep()
    assert closer.closed == []
    assert chunks.closures == []

    clock.advance(timedelta(seconds=1))  # exactly 60s since the one attempt
    drainer.sweep()
    assert closer.closed == [pointer]


def test_sweep_reads_the_clock_once_at_the_top_of_the_pass_for_every_intents_due_check() -> None:
    """A pass judges every intent against the same ``now``, however long the closes take."""
    closer = FakeCloser()
    a = PendingCloseIntent(chunk_id="ch_1", ref=WorkRef(source="default", ref="1"))
    b = PendingCloseIntent(
        chunk_id="ch_2", ref=WorkRef(source="default", ref="2"), attempt_count=1, last_attempt_at=_NOW
    )
    chunks = _FakeCloseChunks([a, b])
    clock = FixedClock(_NOW)

    class _AdvancingCloser(FakeCloser):
        def close(self, pointer: WorkRef, *, trace: DeliveryTrace | None) -> None:
            super().close(pointer, trace=trace)
            clock.advance(timedelta(hours=1))

    advancing = _AdvancingCloser()
    _drainer(chunks, {"default": advancing}, clock).sweep()

    assert advancing.closed == [a.ref]  # b was judged not-due at the pass's own start
    assert closer.closed == []


def _marker(chunk_id: str, name: str, data: str) -> ArtifactRow:
    return ArtifactRow(
        kind=ArtifactKind.ASSET,
        name=name,
        data=data,
        repo=None,
        forge=None,
        artifact_id=f"a_{name}",
        chunk_id=chunk_id,
        node_id="nd_1",
        node_name="merge",
        epoch=1,
    )


def test_sweep_hands_each_closer_the_trace_of_its_chunks_landing_from_one_batched_read() -> None:
    landed = PendingCloseIntent(chunk_id="ch_1", ref=WorkRef(source="default", ref="1"))
    also_landed = PendingCloseIntent(chunk_id="ch_1", ref=WorkRef(source="default", ref="2"), intent_id=2)
    by_hand = PendingCloseIntent(chunk_id="ch_2", ref=WorkRef(source="default", ref="3"), intent_id=3)
    chunks = _FakeCloseChunks([landed, also_landed, by_hand])
    pr_url = "https://forge.example/acme/widget/pull/9"
    chunks.sources["ch_1"] = DeliverySources(
        markers=[
            _marker(
                "ch_1", "delivery-pr/acme/widget/9", json.dumps({"repo": "acme/widget", "number": 9, "url": pr_url})
            ),
            _marker("ch_1", "merged/acme/widget", "abc123def456789"),
        ]
    )
    closer = FakeCloser()

    _drainer(chunks, {"default": closer}, public_url="https://hub.example/").sweep()

    assert chunks.delivery_reads == [["ch_1", "ch_2"]]
    trace = closer.traces[0]
    assert trace is not None
    assert (trace.chunk_id, trace.board_url) == ("ch_1", "https://hub.example/board/chunk/ch_1")
    assert [(g.repo, g.pr_url, g.commit_hash) for g in trace.landings] == [("acme/widget", pr_url, "abc123def456789")]
    assert closer.traces[1] == trace
    assert closer.traces[2] is None  # a hand-completed chunk landed nothing to name


def test_sweep_reads_no_delivery_when_no_intent_is_due() -> None:
    chunks = _FakeCloseChunks([])

    _drainer(chunks, {"default": FakeCloser()}).sweep()

    assert chunks.delivery_reads == []


def test_sweep_of_a_skipped_intent_ticks_the_same_backoff_as_a_failed_one() -> None:
    unopted = PendingCloseIntent(chunk_id="ch_1", ref=WorkRef(source="unopted", ref="1"), intent_id=7)
    chunks = _FakeCloseChunks([unopted])

    _drainer(chunks, {}).sweep()

    assert chunks.skipped_attempts == [7]


@pytest.mark.component
def test_sweep_against_a_real_store_is_idempotent_on_a_second_pass(tmp_path: Path) -> None:
    """Driven twice, then re-read: the second pass issues no second close and writes
    no second fact — the mutation-review re-read (``bzh:mutation-review-selection``). The
    closed arm publishes exactly one ``event-logged`` frame carrying an ``event_log:``
    key, and the redelivered second pass publishes none (event-recording-publishes AC2)."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    closer = FakeCloser()
    registry = WorkSourceRegistry({}, closers={"default": closer})
    drainer = CloseIntentDrainer(
        delivery=cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery),
        artifacts=hub.services.chunks.artifacts,
        events=_event_log(hub),
        work_sources=registry,
        clock=hub.clock,
    )

    drainer.sweep()

    frames = _event_logged_frames(hub)
    assert len(frames) == 1
    assert frames[0]["kind"] == "work-item-closed"
    assert frames[0]["key"].startswith("event_log:")

    drainer.sweep()

    assert closer.closed == [WorkRef(source="default", ref="1")]  # only the first pass actually closed it
    assert hub.services.chunks.delivery.pending_close_intents() == []
    assert len(_event_logged_frames(hub)) == 1  # the redelivered pass published no second frame


@pytest.mark.component
def test_sweep_retries_a_failed_intent_on_the_next_pass_until_it_converges(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    pointer = WorkRef(source="default", ref="1")
    closer = FakeCloser(fail_refs={"1"})
    registry = WorkSourceRegistry({}, closers={"default": closer})
    drainer = CloseIntentDrainer(
        delivery=cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery),
        artifacts=hub.services.chunks.artifacts,
        events=_event_log(hub),
        work_sources=registry,
        clock=hub.clock,
    )

    drainer.sweep()
    assert closer.closed == []
    hub.clock.advance(timedelta(hours=1))  # past the backoff cap — due again

    closer.fail_refs.clear()  # simulate the transient failure clearing before the next sweep
    drainer.sweep()

    assert (
        PendingCloseIntent(chunk_id=chunk_id, ref=pointer) not in hub.services.chunks.delivery.pending_close_intents()
    )
    assert closer.closed == [pointer]


@pytest.mark.component
def test_sweep_over_a_repeated_failure_publishes_one_event_logged_frame(tmp_path: Path) -> None:
    """The failed arm publishes exactly one ``event-logged`` frame carrying an
    ``event_log:`` key, and a second sweep over the same still-failing intent — an
    identical redelivered outcome — publishes none (event-recording-publishes AC2)."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    closer = FakeCloser(fail_refs={"1"})
    registry = WorkSourceRegistry({}, closers={"default": closer})
    drainer = CloseIntentDrainer(
        delivery=cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery),
        artifacts=hub.services.chunks.artifacts,
        events=_event_log(hub),
        work_sources=registry,
        clock=hub.clock,
    )
    pointer = WorkRef(source="default", ref="1")

    drainer.sweep()
    # Not retired: still pending, carrying the attempt it just ticked.
    [pending] = hub.services.chunks.delivery.pending_close_intents()
    assert (pending.ref, pending.attempt_count) == (pointer, 1)

    frames = _event_logged_frames(hub)
    assert len(frames) == 1
    assert frames[0]["kind"] == "work-item-close-failed"
    assert frames[0]["key"].startswith("event_log:")

    hub.clock.advance(timedelta(hours=1))  # past the backoff cap — due again
    drainer.sweep()  # the same failure again — an identical outcome, already recorded

    assert len(_event_logged_frames(hub)) == 1


@pytest.mark.component
def test_sweep_over_an_intent_whose_source_has_no_closer_leaves_it_pending(tmp_path: Path) -> None:
    """A source removed from config after a landing — the only way this arises —
    leaves a stuck pending row rather than dead-lettering it."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    registry = WorkSourceRegistry({}, closers={})  # no closer seated for any source
    drainer = CloseIntentDrainer(
        delivery=cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery),
        artifacts=hub.services.chunks.artifacts,
        events=_event_log(hub),
        work_sources=registry,
        clock=hub.clock,
    )

    drainer.sweep()

    pointer = WorkRef(source="default", ref="1")
    # Just skipped: still pending, carrying the attempt it ticked — not dead-lettered.
    [pending] = hub.services.chunks.delivery.pending_close_intents()
    assert (pending.ref, pending.attempt_count) == (pointer, 1)
    assert pending.chunk_id == chunk_id


# CloseIntentDrainer.sweep() against the built-in `hub` source — always
# seated as a closer, so `build_hub`'s own registry already carries it with no setup.


@pytest.mark.component
def test_sweep_closes_a_landed_hub_born_chunks_item(tmp_path: Path) -> None:
    """Creation itself mints the item's chunk — no separate ingest call
    needed to give the sweep a chunk to land and close against."""
    hub = build_hub(tmp_path)
    created = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()
    pointer = WorkRef(source="hub", ref=created["ref"])
    chunk_id = created["chunk_id"]
    _land(hub, chunk_id)

    hub.services.close_drain.sweep()

    row = WorkItemStore(hub_store_connections(hub.engine)).get("hub", created["ref"])
    assert row is not None
    assert row.closure is WorkItemClosure.DELIVERED
    assert (
        PendingCloseIntent(chunk_id=chunk_id, ref=pointer) not in hub.services.chunks.delivery.pending_close_intents()
    )


@pytest.mark.component
def test_sweep_replayed_over_an_already_delivered_hub_item_is_a_clean_no_op(tmp_path: Path) -> None:
    """The mutation-review re-read (``bzh:mutation-review-selection``): a second pass
    issues no second close and writes no second outcome fact."""
    hub = build_hub(tmp_path)
    created = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()
    chunk_id = created["chunk_id"]
    _land(hub, chunk_id)

    hub.services.close_drain.sweep()
    hub.services.close_drain.sweep()

    pointer = WorkRef(source="hub", ref=created["ref"])
    assert (
        PendingCloseIntent(chunk_id=chunk_id, ref=pointer) not in hub.services.chunks.delivery.pending_close_intents()
    )


# chunk.md — Work refs' closure keys on landing (or a by-hand done), independent of what
# happens to the chunk afterwards, and never on reaching the reserved terminal alone.


@pytest.mark.component
def test_a_chunk_stopped_after_landing_still_closes_its_work_item(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    created = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()
    pointer = WorkRef(source="hub", ref=created["ref"])
    chunk_id = created["chunk_id"]
    _land(hub, chunk_id)

    chunks = hub.services.chunks
    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "alice"}).status_code == 202

    assert PendingCloseIntent(chunk_id=chunk_id, ref=pointer) in chunks.delivery.pending_close_intents()
    hub.services.close_drain.sweep()

    row = WorkItemStore(hub_store_connections(hub.engine)).get("hub", created["ref"])
    assert row is not None
    assert row.closure is WorkItemClosure.DELIVERED
    assert chunks.delivery.pending_close_intents() == []


@pytest.mark.component
def test_a_chunk_reaching_the_terminal_with_no_landing_closes_no_ref(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)

    cast(IWriteChunkMovementRepository, hub.services.chunks.movement).record_transition(
        transition_id="tr_seed_terminal",
        chunk_id=chunk_id,
        from_node_id=None,
        to_node_id=RESERVED_TERMINAL,
        choice_name=None,
        epoch=1,
        runner_id="r1",
        at=hub.clock.now(),
        artifacts=[],
        proposals=[],
        admission=EpochAdmission.AT_OR_ABOVE,
    )

    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None
    assert facts.newest_transition_is_terminal()
    assert hub.services.chunks.delivery.pending_close_intents() == []


def test_sweep_attempts_at_most_the_pass_limit_and_the_rest_drain_on_later_passes() -> None:
    refs = [WorkRef(source="default", ref=str(n)) for n in range(CLOSE_DRAIN_PASS_LIMIT + 5)]
    closer = FakeCloser()
    chunks = _FakeCloseChunks([PendingCloseIntent(chunk_id=f"ch_{n}", ref=ref) for n, ref in enumerate(refs)])
    drainer = _drainer(chunks, {"default": closer})

    drainer.sweep()
    assert closer.closed == refs[:CLOSE_DRAIN_PASS_LIMIT]  # in the read's order; the rest waits

    chunks._candidates = [i for i in chunks._candidates if (i.chunk_id, i.ref) not in chunks.retired]
    drainer.sweep()
    assert closer.closed == refs  # the head moved, so nothing starves
