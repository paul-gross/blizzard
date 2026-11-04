"""The close-intent outbox's drain. ``ChunkDeliveryStore.pending_close_intents()``/
``record_work_item_closure()`` are exercised against a real, migrated store. The enqueue side
(landing/completion) is covered by ``tests/component/hub/store/internal/test_close_intents_enqueue.py``; this file
covers the drain that retires what the enqueue queued."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.event_log import EVENT_LOG_SEVERITY, EventLogKind
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.delivery_read import DeliverySources, DeliveryTrace, PrMarker, board_chunk_url
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.model import (
    Chunk,
    PendingCloseIntent,
    WorkItemCloseOutcome,
    WorkRef,
)
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.commit_links import CommitLink, IReadLandedCommitLinks, no_commit_link
from blizzard.hub.domain.chunk.ports.delivery import IWriteChunkDeliveryRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.work_items.closure import (
    CLOSE_DRAIN_BACKOFF_BASE_SECONDS,
    CLOSE_DRAIN_PASS_LIMIT,
    NO_CLOSER_REASON,
    CloseDrainSweep,
    CloseIntentDrainer,
    TenantCloseIntent,
    close_intent_is_due,
)
from tests.support import (
    FakeCloser,
    WorkSourceRegistry,
)
from tests.support_work_closure import FAKE_TENANT, NOW, sweep_over


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
        return close_intent_is_due(
            last + timedelta(seconds=elapsed), attempt_count=attempt_count, last_attempt_at=last, resume_at=None
        )

    assert not due(threshold_seconds - 1)
    assert due(threshold_seconds)


@pytest.mark.parametrize(("attempt_count", "last_attempt_at"), [(0, None), (None, None), (3, None)])
def test_close_intent_is_due_with_no_prior_attempt(attempt_count: int | None, last_attempt_at: datetime | None) -> None:
    assert close_intent_is_due(
        datetime(2026, 8, 1, tzinfo=UTC), attempt_count=attempt_count, last_attempt_at=last_attempt_at, resume_at=None
    )


def test_the_backoff_base_matches_the_sweep_interval() -> None:
    """The backoff's base second is meant to mirror the close-drain sweep's own tick;
    a drift between the two constants would silently change the
    backoff's real cadence relative to the sweep that drives it."""
    from blizzard.hub.app import CLOSE_DRAIN_INTERVAL_SECONDS

    assert CLOSE_DRAIN_BACKOFF_BASE_SECONDS == CLOSE_DRAIN_INTERVAL_SECONDS


@dataclass
class _RecordedEvent:
    severity: str
    kind: str
    chunk_id: str | None
    message: str
    detail: dict[str, Any] | None
    project_id: str | None = None


class _FakeCloseChunks:
    """The minimal slice of :class:`IWriteChunkDeliveryRepository`/:class:`EventLogService`
    :class:`CloseIntentDrainer` calls. ``record_work_item_closure`` also retires the
    matching intent — recorded rather than persisted, matching the real store's own
    folded transaction."""

    def __init__(self, candidates: list[PendingCloseIntent]) -> None:
        self.candidates = candidates
        self.closures: list[tuple[str, WorkRef, WorkItemCloseOutcome, str | None]] = []
        self.retired: list[tuple[str, WorkRef]] = []
        self.events: list[_RecordedEvent] = []
        self.skipped_attempts: list[int] = []
        self.deferred_attempts: list[tuple[int, datetime]] = []
        self._written: set[tuple[str, str, str, str]] = set()
        self.delivery_reads: list[list[str]] = []
        self.sources: dict[str, DeliverySources] = {}
        self.projects: dict[str, str] = {}
        self.record_reads: list[list[str]] = []

    def get_many(self, chunk_ids: list[str]) -> dict[str, SimpleNamespace]:
        """The record's batched read, holding only the chunks ``projects`` names."""
        self.record_reads.append(list(chunk_ids))
        return {c: SimpleNamespace(chunk_id=c, project_id=self.projects[c]) for c in chunk_ids if c in self.projects}

    def delivery_sources_for(self, chunk_ids: list[str]) -> dict[str, DeliverySources]:
        self.delivery_reads.append(list(chunk_ids))
        return {c: self.sources[c] for c in chunk_ids if c in self.sources}

    def pending_close_intents(self) -> list[TenantCloseIntent]:
        return [TenantCloseIntent(tenant=FAKE_TENANT, intent=intent) for intent in self.candidates]

    def record_close_attempt_skipped(self, intent_id: int, *, at: object) -> None:
        self.skipped_attempts.append(intent_id)

    def record_close_attempt_deferred(self, intent_id: int, *, at: object, resume_at: datetime) -> None:
        self.deferred_attempts.append((intent_id, resume_at))

    def record_work_item_closure(
        self, chunk_id: str, *, pointer: WorkRef, outcome: WorkItemCloseOutcome, reason: str | None, at: object
    ) -> bool:
        if outcome in (WorkItemCloseOutcome.CLOSED, WorkItemCloseOutcome.GONE, WorkItemCloseOutcome.SKIPPED):
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
        project_id: str | None,
        lease_id: str | None,
        node_name: str | None,
        message: str,
        detail: dict[str, Any] | None,
        at: object,
    ) -> int:
        self.events.append(
            _RecordedEvent(
                severity=EVENT_LOG_SEVERITY[kind],
                kind=kind,
                chunk_id=chunk_id,
                project_id=project_id,
                message=message,
                detail=detail,
            )
        )
        return len(self.events)


class _NoCommitLinks:
    def for_chunks(self, chunks: Sequence[Chunk]) -> dict[str, CommitLink]:
        return {chunk.chunk_id: no_commit_link for chunk in chunks}


@dataclass
class _ForgeCommitLinks:
    """Links every landed commit of each chunk it is asked for under ``origin``, recording each batch it serves."""

    origin: str
    reads: list[list[str]]

    def for_chunks(self, chunks: Sequence[Chunk]) -> dict[str, CommitLink]:
        self.reads.append([chunk.chunk_id for chunk in chunks])
        return {chunk.chunk_id: lambda repo, sha: f"{self.origin}/{repo}/commit/{sha}" for chunk in chunks}


def _as_delivery(chunks: _FakeCloseChunks) -> IWriteChunkDeliveryRepository:
    return cast(IWriteChunkDeliveryRepository, chunks)


def _as_events(chunks: _FakeCloseChunks) -> EventLogService:
    return cast(EventLogService, chunks)


def _drainer(
    chunks: _FakeCloseChunks,
    closers: dict[str, FakeCloser],
    clock: FixedClock | None = None,
    public_url: str | None = None,
    slugs: dict[str, str] | None = None,
    commit_links: IReadLandedCommitLinks | None = None,
) -> CloseDrainSweep:
    registry = WorkSourceRegistry({}, closers=closers, slugs=slugs)
    clock = clock or FixedClock(NOW)
    return sweep_over(
        chunks,
        CloseIntentDrainer(
            delivery=_as_delivery(chunks),
            artifacts=cast(IReadChunkArtifactsRepository, chunks),
            chunks=cast(IReadChunkRecordRepository, chunks),
            events=_as_events(chunks),
            work_sources=registry,
            clock=clock,
            commit_links=commit_links or _NoCommitLinks(),
            public_url=public_url,
        ),
        clock,
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


def test_sweep_records_the_closed_and_failed_events_with_their_exact_message_and_detail() -> None:
    closed_ref = WorkRef(source="default", ref="1")
    failed_ref = WorkRef(source="default", ref="2")
    chunks = _FakeCloseChunks(
        [
            PendingCloseIntent(chunk_id="ch_1", ref=closed_ref, intent_id=1),
            PendingCloseIntent(chunk_id="ch_2", ref=failed_ref, intent_id=2),
        ]
    )

    _drainer(chunks, {"default": FakeCloser(fail_refs={"2"})}).sweep()

    assert chunks.events == [
        _RecordedEvent(
            severity="info", kind="work-item-closed", chunk_id="ch_1", message="closed default#1", detail=None
        ),
        _RecordedEvent(
            severity="warning",
            kind="work-item-close-failed",
            chunk_id="ch_2",
            message="failed to close default#2: boom closing 2",
            detail={"outcome": "failed", "reason": "boom closing 2"},
        ),
    ]


def test_sweep_s_events_name_the_ref_by_its_source_s_current_slug() -> None:
    """A close event renders the source's slug as it stands when the event is written, not the stored key."""
    ref = WorkRef(source="wsrc_default", ref="1")
    chunks = _FakeCloseChunks([PendingCloseIntent(chunk_id="ch_1", ref=ref)])

    _drainer(chunks, {"wsrc_default": FakeCloser()}, slugs={"wsrc_default": "default-core"}).sweep()

    assert [event.message for event in chunks.events] == ["closed default-core#1"]


def test_sweep_builds_each_traces_board_url_through_the_shared_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    def via_helper(public_url: str | None, chunk_id: str) -> str:
        return f"via-helper:{public_url}:{chunk_id}"

    monkeypatch.setattr("blizzard.hub.domain.work_items.closure.board_chunk_url", via_helper)
    chunks = _FakeCloseChunks([PendingCloseIntent(chunk_id="ch_1", ref=WorkRef(source="default", ref="1"))])
    chunks.sources["ch_1"] = DeliverySources(markers=[_marker("ch_1", "merged/acme/widget", "abc123def456789")])
    closer = FakeCloser()

    _drainer(chunks, {"default": closer}, public_url="https://hub.example/").sweep()

    trace = closer.traces[0]
    assert trace is not None
    assert trace.board_url == "via-helper:https://hub.example/:ch_1"


def test_board_chunk_url_trims_the_trailing_slash_and_is_absent_without_a_public_url() -> None:
    assert board_chunk_url("https://hub.example/", "ch_1") == "https://hub.example/board/chunk/ch_1"
    assert board_chunk_url("https://hub.example", "ch_1") == "https://hub.example/board/chunk/ch_1"
    assert board_chunk_url(None, "ch_1") is None
    assert board_chunk_url("", "ch_1") is None


def test_sweep_leaves_a_failed_intent_pending_and_retries_it() -> None:
    ref = WorkRef(source="default", ref="1")
    closer = FakeCloser(fail_refs={"1"})
    chunks = _FakeCloseChunks([PendingCloseIntent(chunk_id="ch_1", ref=ref)])

    _drainer(chunks, {"default": closer}).sweep()

    assert chunks.closures == [("ch_1", ref, WorkItemCloseOutcome.FAILED, "boom closing 1")]
    assert chunks.retired == []


def test_sweep_defers_a_close_the_rate_budget_turns_away_without_a_closure_fact_or_backoff() -> None:
    ref = WorkRef(source="default", ref="1")
    resume_at = NOW + timedelta(minutes=5)
    closer = FakeCloser(rate_refs={"1": resume_at})
    chunks = _FakeCloseChunks([PendingCloseIntent(chunk_id="ch_1", ref=ref, intent_id=7)])

    with capture_logs() as logs:
        _drainer(chunks, {"default": closer}).sweep()

    assert chunks.deferred_attempts == [(7, resume_at)]
    assert (chunks.closures, chunks.retired, chunks.events, chunks.skipped_attempts) == ([], [], [], [])
    [summary] = [entry for entry in logs if entry["event"] == "close intent drain sweep completed"]
    assert (summary["deferred"], summary["failed"]) == (1, 0)


def test_sweep_does_not_attempt_a_deferred_intent_before_its_resume_at() -> None:
    pointer = WorkRef(source="default", ref="1")
    closer = FakeCloser()
    deferred = PendingCloseIntent(chunk_id="ch_1", ref=pointer, resume_at=NOW + timedelta(minutes=5))
    clock = FixedClock(NOW + timedelta(minutes=5) - timedelta(seconds=1))
    drainer = _drainer(_FakeCloseChunks([deferred]), {"default": closer}, clock)

    drainer.sweep()
    assert closer.closed == []

    clock.advance(timedelta(seconds=1))
    drainer.sweep()
    assert closer.closed == [pointer]


def test_sweep_skips_an_intent_whose_source_has_no_closer_bound() -> None:
    """A legacy intent without a closer is terminally skipped."""
    unopted_ref = WorkRef(source="unopted", ref="1")
    chunks = _FakeCloseChunks([PendingCloseIntent(chunk_id="ch_1", ref=unopted_ref, intent_id=7)])

    _drainer(chunks, {}).sweep()

    assert chunks.closures == [("ch_1", unopted_ref, WorkItemCloseOutcome.SKIPPED, NO_CLOSER_REASON)]
    assert chunks.retired == [("ch_1", unopted_ref)]
    assert [event.kind for event in chunks.events] == ["work-item-close-skipped"]
    assert chunks.skipped_attempts == []


def test_sweep_over_an_empty_queue_issues_no_forge_call() -> None:
    closer = FakeCloser()

    _drainer(_FakeCloseChunks([]), {"default": closer}).sweep()

    assert closer.closed == []


def test_sweep_does_not_attempt_a_backed_off_intent_until_its_threshold_elapses() -> None:
    pointer = WorkRef(source="default", ref="1")
    closer = FakeCloser()
    backed_off = PendingCloseIntent(chunk_id="ch_1", ref=pointer, attempt_count=1, last_attempt_at=NOW)
    chunks = _FakeCloseChunks([backed_off])
    clock = FixedClock(NOW + timedelta(seconds=59))
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
        chunk_id="ch_2", ref=WorkRef(source="default", ref="2"), attempt_count=1, last_attempt_at=NOW
    )
    chunks = _FakeCloseChunks([a, b])
    clock = FixedClock(NOW)

    class _AdvancingCloser(FakeCloser):
        def close(self, pointer: WorkRef, *, trace: DeliveryTrace | None) -> None:
            super().close(pointer, trace=trace)
            clock.advance(timedelta(hours=1))

    advancing = _AdvancingCloser()
    _drainer(chunks, {"default": advancing}, clock).sweep()

    assert advancing.closed == [a.ref]  # b was judged not-due at the pass's own start
    assert closer.closed == []


def _marker(chunk_id: str, name: str, data: str) -> StoredArtifact:
    return StoredArtifact(
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
        markers=[_marker("ch_1", "merged/acme/widget", "abc123def456789")],
        pr_markers=[PrMarker("delivery-pr/acme/widget/9", 1, "acme/widget", 9, pr_url)],
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


def test_sweep_hands_each_closer_the_record_forges_commit_url_for_a_landing_with_no_pr_from_one_batched_read() -> None:
    landed = PendingCloseIntent(chunk_id="ch_1", ref=WorkRef(source="default", ref="1"))
    other = PendingCloseIntent(chunk_id="ch_2", ref=WorkRef(source="default", ref="2"), intent_id=2)
    chunks = _FakeCloseChunks([landed, other])
    chunks.projects.update({"ch_1": "prj_widget", "ch_2": "prj_widget"})
    chunks.sources["ch_1"] = DeliverySources(markers=[_marker("ch_1", "merged/acme/widget", "abc123")])
    links = _ForgeCommitLinks("https://forge.example", [])
    closer = FakeCloser()

    _drainer(chunks, {"default": closer}, commit_links=links).sweep()

    assert links.reads == [["ch_1", "ch_2"]]
    assert chunks.record_reads == [["ch_1", "ch_2"]]
    trace = closer.traces[0]
    assert trace is not None
    assert [(g.repo, g.pr_url, g.commit_url) for g in trace.landings] == [
        ("acme/widget", None, "https://forge.example/acme/widget/commit/abc123")
    ]


def test_sweep_resolves_the_projects_of_its_chunks_in_one_batched_read_and_names_none_for_a_gone_chunk() -> None:
    present = PendingCloseIntent(chunk_id="ch_1", ref=WorkRef(source="default", ref="1"))
    also_present = PendingCloseIntent(chunk_id="ch_1", ref=WorkRef(source="default", ref="2"), intent_id=2)
    gone = PendingCloseIntent(chunk_id="ch_2", ref=WorkRef(source="default", ref="3"), intent_id=3)
    chunks = _FakeCloseChunks([present, also_present, gone])
    chunks.projects["ch_1"] = "prj_widget"

    _drainer(chunks, {"default": FakeCloser()}).sweep()

    assert chunks.record_reads == [["ch_1", "ch_2"]]
    assert [(e.chunk_id, e.project_id) for e in chunks.events] == [
        ("ch_1", "prj_widget"),
        ("ch_1", "prj_widget"),
        ("ch_2", None),
    ]


def test_sweep_reads_no_delivery_when_no_intent_is_due() -> None:
    chunks = _FakeCloseChunks([])

    _drainer(chunks, {"default": FakeCloser()}).sweep()

    assert chunks.delivery_reads == []
    assert chunks.record_reads == []


def test_sweep_of_a_skipped_intent_retires_it() -> None:
    unopted = PendingCloseIntent(chunk_id="ch_1", ref=WorkRef(source="unopted", ref="1"), intent_id=7)
    chunks = _FakeCloseChunks([unopted])

    _drainer(chunks, {}).sweep()

    assert chunks.retired == [("ch_1", unopted.ref)]


def test_sweep_attempts_at_most_the_pass_limit_and_the_rest_drain_on_later_passes() -> None:
    refs = [WorkRef(source="default", ref=str(n)) for n in range(CLOSE_DRAIN_PASS_LIMIT + 5)]
    closer = FakeCloser()
    chunks = _FakeCloseChunks([PendingCloseIntent(chunk_id=f"ch_{n}", ref=ref) for n, ref in enumerate(refs)])
    drainer = _drainer(chunks, {"default": closer})

    drainer.sweep()
    assert closer.closed == refs[:CLOSE_DRAIN_PASS_LIMIT]  # in the read's order; the rest waits

    chunks.candidates = [i for i in chunks.candidates if (i.chunk_id, i.ref) not in chunks.retired]
    drainer.sweep()
    assert closer.closed == refs  # the head moved, so nothing starves


def test_sweep_logs_one_summary_counting_each_outcome() -> None:
    refs = {name: WorkRef(source="default", ref=name) for name in ("ok", "gone", "bad")}
    chunks = _FakeCloseChunks(
        [PendingCloseIntent(chunk_id=f"ch_{name}", ref=ref) for name, ref in refs.items()]
        + [PendingCloseIntent(chunk_id="ch_skip", ref=WorkRef(source="unopted", ref="1"), intent_id=9)]
    )
    closer = FakeCloser(gone_refs={"gone"}, fail_refs={"bad"})

    with capture_logs() as logs:
        _drainer(chunks, {"default": closer}).sweep()

    [summary] = [entry for entry in logs if entry["event"] == "close intent drain sweep completed"]
    assert (summary["closed"], summary["gone"], summary["failed"], summary["skipped"]) == (1, 1, 1, 1)
