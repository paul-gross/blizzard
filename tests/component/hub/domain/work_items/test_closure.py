"""The close-intent outbox's drain. ``ChunkDeliveryStore.pending_close_intents()``/
``record_work_item_closure()`` are exercised against a real, migrated store. The enqueue side
(landing/completion) is covered by ``tests/component/hub/store/internal/test_close_intents_enqueue.py``; this file
covers the drain that retires what the enqueue queued."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import sqlalchemy as sa

from blizzard.foundation.hub_event_types import HubEventType
from blizzard.foundation.work_items import WorkItemClosure
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.model import (
    CloseEnqueueDecision,
    PendingCloseIntent,
    WorkItemCloseOutcome,
    WorkRef,
)
from blizzard.hub.domain.chunk.ports.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.delivery import IWriteChunkDeliveryRepository
from blizzard.hub.domain.chunk.ports.events import IWriteChunkEventsRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from blizzard.hub.domain.work_items.closure import (
    CloseIntentDrainer,
)
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.hub_scoped_corpus import SweepCorpusReads
from blizzard.hub.store.internal.hub_scoped_reads import build_hub_scoped_reads
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support.scaffolding import (
    FakeCloser,
    HubHarness,
    WorkSourceRegistry,
    build_hub,
    count_queries,
    emitted_events,
    hub_level_connections,
    hub_scoped,
    hub_store_connections,
    ingest,
)
from tests.support.work_closure import sweep_over


def _event_log(hub: HubHarness) -> EventLogService:
    """A real :class:`EventLogService` over the harness's own store and broker, for a
    test that wires its own :class:`CloseIntentDrainer` rather than taking
    ``hub.services.close_drain``."""
    return EventLogService(
        events=cast(IWriteChunkEventsRepository, hub.services.chunks.events),
        publisher=hub.events,
    )


def _event_logged_frames(hub: HubHarness, *, since: int = 0) -> list[dict[str, Any]]:
    return [json.loads(e["data"]) for e in emitted_events(hub, since=since) if e["event"] == HubEventType.EVENT_LOGGED]


def _land(hub: HubHarness, chunk_id: str, *, repo: str = "widget") -> None:
    """Simulate a generic hub command node's mid-run ``merged/<repo>`` marker —
    the current landing truth :func:`~blizzard.hub.domain.chunk.model.has_landed_repos` reads
    independent of any real graph/node machinery. Enqueues a pending close
    intent as a side effect of the same write."""
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    cast(IWriteChunkArtifactsRepository, hub.services.chunks.artifacts).record_hub_artifact(
        chunk_id,
        node_id="nd_deliver",
        node_name="deliver",
        epoch=1,
        name=f"merged/{repo}",
        content="sha",
        at=hub.clock.now(),
        admission=EpochAdmission.AT_OR_ABOVE,
        close_decision=CloseEnqueueDecision(closable=frozenset(chunk.work_refs), skipped=frozenset()),
    )


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
    delivery.record_work_item_closure(
        chunk_id, pointer=fresh.ref, outcome=WorkItemCloseOutcome.FAILED, reason="x", at=hub.clock.now()
    )

    [backed_off] = delivery.pending_close_intents()
    assert backed_off.intent_id == fresh.intent_id
    assert backed_off.attempt_count == 2
    assert backed_off.last_attempt_at == hub.clock.now()
    assert backed_off.resume_at is None


def test_pending_close_intents_leaves_deferred_attempts_out_and_carries_the_newest_resume_at(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    delivery = cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery)
    [fresh] = delivery.pending_close_intents()
    failed_at = hub.clock.now()
    delivery.record_work_item_closure(
        chunk_id, pointer=fresh.ref, outcome=WorkItemCloseOutcome.FAILED, reason="x", at=failed_at
    )
    first_resume, newest_resume = failed_at + timedelta(minutes=30), failed_at + timedelta(minutes=5)
    hub.clock.advance(timedelta(seconds=10))
    delivery.record_close_attempt_deferred(fresh.intent_id, at=hub.clock.now(), resume_at=first_resume)
    hub.clock.advance(timedelta(seconds=10))
    delivery.record_close_attempt_deferred(fresh.intent_id, at=hub.clock.now(), resume_at=newest_resume)

    [deferred] = delivery.pending_close_intents()
    assert (deferred.attempt_count, deferred.last_attempt_at, deferred.resume_at) == (1, failed_at, newest_resume)
    [row] = _corpus(hub).pending_close_intents()
    assert (row.intent.attempt_count, row.intent.last_attempt_at, row.intent.resume_at) == (
        1,
        failed_at,
        newest_resume,
    )


def test_pending_close_intents_of_an_intent_only_ever_deferred_reads_as_never_attempted(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    delivery = cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery)
    [fresh] = delivery.pending_close_intents()
    resume_at = hub.clock.now() + timedelta(minutes=5)
    delivery.record_close_attempt_deferred(fresh.intent_id, at=hub.clock.now(), resume_at=resume_at)

    [deferred] = delivery.pending_close_intents()
    assert (deferred.attempt_count, deferred.last_attempt_at, deferred.resume_at) == (0, None, resume_at)
    [row] = _corpus(hub).pending_close_intents()
    assert (row.intent.attempt_count, row.intent.last_attempt_at, row.intent.resume_at) == (0, None, resume_at)


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
    for intent in delivery.pending_close_intents():
        delivery.record_close_attempt_deferred(intent.intent_id, at=hub.clock.now(), resume_at=hub.clock.now())

    count = count_queries(hub.engine, delivery.pending_close_intents)
    assert count == 1


def _corpus(hub: HubHarness) -> SweepCorpusReads:
    return build_hub_scoped_reads(hub_level_connections(hub.engine)).corpus


def test_sweep_against_a_real_store_defers_a_turned_away_close_to_its_resume_at(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    resume_at = hub.clock.now() + timedelta(minutes=5)
    closer = FakeCloser(rate_refs={"1": resume_at})
    delivery = cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery)
    drainer = sweep_over(
        _corpus(hub),
        CloseIntentDrainer(
            delivery=delivery,
            artifacts=hub.services.chunks.artifacts,
            chunks=hub.services.chunks.record,
            events=_event_log(hub),
            work_sources=WorkSourceRegistry({}, closers={"default": closer}),
            clock=hub.clock,
            commit_links=hub.services.commit_links,
        ),
        hub.clock,
    )

    drainer.sweep()

    [held] = delivery.pending_close_intents()
    assert (held.attempt_count, held.last_attempt_at, held.resume_at) == (0, None, resume_at)
    assert _event_logged_frames(hub) == []
    with hub.engine.connect() as conn:
        assert conn.execute(hub_scoped(sa.select(sa.func.count()).select_from(s.work_item_closures))).scalar_one() == 0

    closer.rate_refs.clear()
    hub.clock.advance(timedelta(minutes=5) - timedelta(seconds=1))
    drainer.sweep()
    assert closer.closed == []

    hub.clock.advance(timedelta(seconds=1))
    drainer.sweep()
    assert closer.closed == [WorkRef(source="default", ref="1")]
    assert delivery.pending_close_intents() == []


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
    drainer = sweep_over(
        _corpus(hub),
        CloseIntentDrainer(
            delivery=cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery),
            artifacts=hub.services.chunks.artifacts,
            chunks=hub.services.chunks.record,
            events=_event_log(hub),
            work_sources=registry,
            clock=hub.clock,
            commit_links=hub.services.commit_links,
        ),
        hub.clock,
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


def test_sweep_retries_a_failed_intent_on_the_next_pass_until_it_converges(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    pointer = WorkRef(source="default", ref="1")
    closer = FakeCloser(fail_refs={"1"})
    registry = WorkSourceRegistry({}, closers={"default": closer})
    drainer = sweep_over(
        _corpus(hub),
        CloseIntentDrainer(
            delivery=cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery),
            artifacts=hub.services.chunks.artifacts,
            chunks=hub.services.chunks.record,
            events=_event_log(hub),
            work_sources=registry,
            clock=hub.clock,
            commit_links=hub.services.commit_links,
        ),
        hub.clock,
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


def test_sweep_over_a_repeated_failure_publishes_one_event_logged_frame(tmp_path: Path) -> None:
    """The failed arm publishes exactly one ``event-logged`` frame carrying an
    ``event_log:`` key, and a second sweep over the same still-failing intent — an
    identical redelivered outcome — publishes none (event-recording-publishes AC2)."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    closer = FakeCloser(fail_refs={"1"})
    registry = WorkSourceRegistry({}, closers={"default": closer})
    drainer = sweep_over(
        _corpus(hub),
        CloseIntentDrainer(
            delivery=cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery),
            artifacts=hub.services.chunks.artifacts,
            chunks=hub.services.chunks.record,
            events=_event_log(hub),
            work_sources=registry,
            clock=hub.clock,
            commit_links=hub.services.commit_links,
        ),
        hub.clock,
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


def test_sweep_over_an_intent_whose_source_has_no_closer_retires_it(tmp_path: Path) -> None:
    """A source removed after landing leaves a terminal skipped outcome."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    registry = WorkSourceRegistry({}, closers={})  # no closer seated for any source
    drainer = sweep_over(
        _corpus(hub),
        CloseIntentDrainer(
            delivery=cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery),
            artifacts=hub.services.chunks.artifacts,
            chunks=hub.services.chunks.record,
            events=_event_log(hub),
            work_sources=registry,
            clock=hub.clock,
            commit_links=hub.services.commit_links,
        ),
        hub.clock,
    )

    drainer.sweep()

    assert hub.services.chunks.delivery.pending_close_intents() == []
    frames = _event_logged_frames(hub)
    assert [frame["kind"] for frame in frames] == ["work-item-close-skipped"]
    drainer.sweep()
    assert len(_event_logged_frames(hub)) == 1


def test_mixed_legacy_intents_close_and_skip_once(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    bound = ingest(hub, [{"source": "default", "ref": "1"}])
    created = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()
    unbound = created["chunk_id"]
    _land(hub, bound)
    _land(hub, unbound)
    closer = FakeCloser()
    drainer = sweep_over(
        _corpus(hub),
        CloseIntentDrainer(
            delivery=cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery),
            artifacts=hub.services.chunks.artifacts,
            chunks=hub.services.chunks.record,
            events=_event_log(hub),
            work_sources=WorkSourceRegistry({}, closers={"default": closer}),
            clock=hub.clock,
            commit_links=hub.services.commit_links,
        ),
        hub.clock,
    )

    drainer.sweep()
    drainer.sweep()

    assert closer.closed == [WorkRef("default", "1")]
    assert hub.services.chunks.delivery.pending_close_intents() == []
    assert sorted(frame["kind"] for frame in _event_logged_frames(hub)) == [
        "work-item-close-skipped",
        "work-item-closed",
    ]


def test_sweep_closes_a_landed_hub_born_chunks_item(tmp_path: Path) -> None:
    """Creation itself mints the item's chunk — no separate ingest call
    needed to give the sweep a chunk to land and close against."""
    hub = build_hub(tmp_path)
    created = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()
    pointer = WorkRef(source="hub", ref=created["ref"])
    chunk_id = created["chunk_id"]
    _land(hub, chunk_id)

    hub.hub_services.close_drain.sweep()

    row = WorkItemStore(hub_store_connections(hub.engine)).get("hub", created["ref"])
    assert row is not None
    assert row.closure is WorkItemClosure.DELIVERED
    assert (
        PendingCloseIntent(chunk_id=chunk_id, ref=pointer) not in hub.services.chunks.delivery.pending_close_intents()
    )


def test_sweep_replayed_over_an_already_delivered_hub_item_is_a_clean_no_op(tmp_path: Path) -> None:
    """The mutation-review re-read (``bzh:mutation-review-selection``): a second pass
    issues no second close and writes no second outcome fact."""
    hub = build_hub(tmp_path)
    created = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()
    chunk_id = created["chunk_id"]
    _land(hub, chunk_id)

    hub.hub_services.close_drain.sweep()
    hub.hub_services.close_drain.sweep()

    pointer = WorkRef(source="hub", ref=created["ref"])
    assert (
        PendingCloseIntent(chunk_id=chunk_id, ref=pointer) not in hub.services.chunks.delivery.pending_close_intents()
    )


def test_a_chunk_stopped_after_landing_still_closes_its_work_item(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    created = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()
    pointer = WorkRef(source="hub", ref=created["ref"])
    chunk_id = created["chunk_id"]
    _land(hub, chunk_id)

    chunks = hub.services.chunks
    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "alice"}).status_code == 202

    assert PendingCloseIntent(chunk_id=chunk_id, ref=pointer) in chunks.delivery.pending_close_intents()
    hub.hub_services.close_drain.sweep()

    row = WorkItemStore(hub_store_connections(hub.engine)).get("hub", created["ref"])
    assert row is not None
    assert row.closure is WorkItemClosure.DELIVERED
    assert chunks.delivery.pending_close_intents() == []


def test_a_chunk_reaching_the_terminal_with_no_landing_enqueues_its_ref(tmp_path: Path) -> None:
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
        admission=EpochAdmission.AT_OR_ABOVE,
        close_decision=CloseEnqueueDecision(closable=frozenset({WorkRef("default", "1")}), skipped=frozenset()),
    )

    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None
    assert facts.newest_transition_is_terminal()
    assert hub.services.chunks.delivery.pending_close_intents() == [
        PendingCloseIntent(chunk_id=chunk_id, ref=WorkRef(source="default", ref="1"))
    ]
