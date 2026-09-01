"""The close-intent outbox's drain (blizzard#383). ``ChunkDeliveryStore.pending_close_intents()``/
``record_work_item_closure()`` are exercised against a real, migrated store. The enqueue side
(landing/completion, D1) is covered by ``tests/test_close_intents_enqueue.py``; this file
covers the drain that retires what the enqueue queued."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunks.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunks.delivery import IWriteChunkDeliveryRepository
from blizzard.hub.domain.chunks.events import IWriteChunkEventsRepository
from blizzard.hub.domain.work import (
    PendingCloseIntent,
    WorkItemCloseOutcome,
    WorkItemClosure,
    WorkRef,
)
from blizzard.hub.domain.work_closure import CloseIntentDrainer
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.work_sources.registry import WorkSourceRegistry
from tests.support import FakeCloser, HubHarness, build_hub, hub_store_connections, ingest

pytestmark = pytest.mark.unit


def _land(hub: HubHarness, chunk_id: str, *, repo: str = "widget") -> None:
    """Simulate a generic hub command node's mid-run ``merged/<repo>`` marker —
    the current landing truth :func:`~blizzard.hub.domain.work.has_landed_repos` reads
    (issue #67), independent of any real graph/node machinery. Enqueues a pending close
    intent (D1) as a side effect of the same write."""
    cast(IWriteChunkArtifactsRepository, hub.services.chunks.artifacts).record_hub_artifact(
        chunk_id,
        node_id="nd_deliver",
        node_name="deliver",
        epoch=1,
        name=f"merged/{repo}",
        content="sha",
        at=hub.clock.now(),
    )


# ChunkDeliveryStore.record_work_item_closure() — retires its matching intent in the same
# transaction (blizzard#383, F8/F9) whenever the outcome is closed/gone.


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
    """The crash-recovery case (F9): the outcome was already recorded on a prior pass — the
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

    assert PendingCloseIntent(chunk_id=chunk_id, ref=pointer) in hub.services.chunks.delivery.pending_close_intents()


# CloseIntentDrainer.sweep() — fakes (unit tier)


@dataclass
class _RecordedEvent:
    severity: str
    kind: str
    chunk_id: str | None
    message: str
    detail: dict | None


class _FakeCloseChunks:
    """The minimal slice of :class:`IWriteChunkDeliveryRepository`/:class:`IWriteChunkEventsRepository`
    :class:`CloseIntentDrainer` calls. ``record_work_item_closure`` also retires the
    matching intent — recorded rather than persisted, matching the real store's own
    folded transaction."""

    def __init__(self, candidates: list[PendingCloseIntent]) -> None:
        self._candidates = candidates
        self.closures: list[tuple[str, WorkRef, WorkItemCloseOutcome, str | None]] = []
        self.retired: list[tuple[str, WorkRef]] = []
        self.events: list[_RecordedEvent] = []
        self._written: set[tuple[str, str, str, str]] = set()

    def pending_close_intents(self) -> list[PendingCloseIntent]:
        return list(self._candidates)

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

    def record_event(
        self,
        *,
        severity: str,
        kind: str,
        runner_id: str,
        chunk_id: str | None,
        lease_id: str | None,
        node_name: str | None,
        message: str,
        detail: dict | None,
        at: object,
    ) -> int:
        self.events.append(
            _RecordedEvent(severity=severity, kind=kind, chunk_id=chunk_id, message=message, detail=detail)
        )
        return len(self.events)


def _as_delivery(chunks: _FakeCloseChunks) -> IWriteChunkDeliveryRepository:
    return cast(IWriteChunkDeliveryRepository, chunks)


def _as_events(chunks: _FakeCloseChunks) -> IWriteChunkEventsRepository:
    return cast(IWriteChunkEventsRepository, chunks)


def _drainer(chunks: _FakeCloseChunks, closers: dict[str, FakeCloser]) -> CloseIntentDrainer:
    registry = WorkSourceRegistry({}, closers=closers)  # type: ignore[arg-type]
    clock = FixedClock(datetime(2026, 8, 1, tzinfo=UTC))
    return CloseIntentDrainer(
        delivery=_as_delivery(chunks), events=_as_events(chunks), work_sources=registry, clock=clock
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
    """D4: an intent from a source not seated as a closer stays pending, untouched —
    the sweep neither closes it nor retires it, and issues no forge call."""
    unopted_ref = WorkRef(source="unopted", ref="1")
    chunks = _FakeCloseChunks([PendingCloseIntent(chunk_id="ch_1", ref=unopted_ref)])

    _drainer(chunks, {}).sweep()

    assert chunks.closures == []
    assert chunks.retired == []
    assert chunks.events == []


def test_sweep_over_an_empty_queue_issues_no_forge_call() -> None:
    closer = FakeCloser()

    _drainer(_FakeCloseChunks([]), {"default": closer}).sweep()

    assert closer.closed == []


# CloseIntentDrainer.sweep() — real store + FakeCloser (component tier)


@pytest.mark.component
def test_sweep_against_a_real_store_is_idempotent_on_a_second_pass(tmp_path: Path) -> None:
    """Driven twice, then re-read: the second pass issues no second close and writes
    no second fact — the mutation-review re-read (``bzh:mutation-review-selection``)."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    closer = FakeCloser()
    registry = WorkSourceRegistry({}, closers={"default": closer})
    drainer = CloseIntentDrainer(
        delivery=cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery),
        events=cast(IWriteChunkEventsRepository, hub.services.chunks.events),
        work_sources=registry,
        clock=hub.clock,
    )

    drainer.sweep()
    drainer.sweep()

    assert closer.closed == [WorkRef(source="default", ref="1")]  # only the first pass actually closed it
    assert hub.services.chunks.delivery.pending_close_intents() == []


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
        events=cast(IWriteChunkEventsRepository, hub.services.chunks.events),
        work_sources=registry,
        clock=hub.clock,
    )

    drainer.sweep()
    assert PendingCloseIntent(chunk_id=chunk_id, ref=pointer) in hub.services.chunks.delivery.pending_close_intents()

    closer.fail_refs.clear()  # simulate the transient failure clearing before the next sweep
    drainer.sweep()

    assert (
        PendingCloseIntent(chunk_id=chunk_id, ref=pointer) not in hub.services.chunks.delivery.pending_close_intents()
    )
    assert closer.closed == [pointer]


@pytest.mark.component
def test_sweep_over_an_intent_whose_source_has_no_closer_leaves_it_pending(tmp_path: Path) -> None:
    """D4: a source removed from config after a landing — the only way this arises —
    leaves a stuck pending row rather than dead-lettering it."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    registry = WorkSourceRegistry({}, closers={})  # no closer seated for any source
    drainer = CloseIntentDrainer(
        delivery=cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery),
        events=cast(IWriteChunkEventsRepository, hub.services.chunks.events),
        work_sources=registry,
        clock=hub.clock,
    )

    drainer.sweep()

    pointer = WorkRef(source="default", ref="1")
    assert PendingCloseIntent(chunk_id=chunk_id, ref=pointer) in hub.services.chunks.delivery.pending_close_intents()


# CloseIntentDrainer.sweep() against the built-in `hub` source (issue #360) — always
# seated as a closer, so `build_hub`'s own registry already carries it with no setup.


@pytest.mark.component
def test_sweep_closes_a_landed_hub_born_chunks_item(tmp_path: Path) -> None:
    """Creation itself mints the item's chunk (blizzard#359) — no separate ingest call
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
