"""The delivery closure fact and reconciler (issue #216).

``ChunkStore.closable_work_refs()``/``record_work_item_closure()`` are exercised
against a real, migrated store (component tier) — the same choice
``tests/test_forge_status.py`` makes for ``live_work_refs()``, its own structural
twin. The landing gate is ``has_landed_repos`` alone, not chunk status: a chunk that
landed and was *later* stopped still owes a closure attempt, while one that never
landed never does, whether or not it is stopped (the plan's own recorded deviation
from an inline deliver-step close).

``DeliveryClosureReconciler.sweep()`` is exercised twice: over fakes (unit tier,
mirroring ``AnnotationReconciler``'s own fakes-first split in this same file's sibling)
for the diff/dispatch logic, then against the real store + ``tests.support.FakeCloser``
(component tier) for the idempotent double-sweep and failed-then-converges-to-closed
retry — the mutation-review re-read (``bzh:mutation-review-selection``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.work import ClosableWorkRef, IWriteChunkRepository, WorkItemCloseOutcome, WorkRef
from blizzard.hub.domain.work_closure import DeliveryClosureReconciler
from blizzard.hub.work_sources.registry import WorkSourceRegistry
from tests.support import FakeCloser, HubHarness, build_hub, ingest

pytestmark = pytest.mark.unit


def _writable(hub: HubHarness) -> IWriteChunkRepository:
    """A test-only cast, mirroring ``tests/test_hub_command_node.py``'s own helper:
    ``HubHarness.services.chunks`` is read-typed, but the live object is always the
    write-capable ``ChunkStore``."""
    return cast(IWriteChunkRepository, hub.services.chunks)


def _land(hub: HubHarness, chunk_id: str, *, repo: str = "widget") -> None:
    """Simulate a generic hub command node's mid-run ``merged/<repo>`` marker —
    the current landing truth :func:`~blizzard.hub.domain.work.has_landed_repos` reads
    (issue #67), independent of any real graph/node machinery."""
    _writable(hub).record_hub_artifact(
        chunk_id,
        node_id="nd_deliver",
        node_name="deliver",
        epoch=1,
        name=f"merged/{repo}",
        content="sha",
        at=hub.clock.now(),
    )


# --------------------------------------------------------------------------- #
# IReadChunkRepository.closable_work_refs() — real ChunkStore, real migrations
# --------------------------------------------------------------------------- #


@pytest.mark.component
def test_closable_work_refs_includes_a_landed_chunks_refs(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)

    refs = hub.services.chunks.closable_work_refs()

    assert ClosableWorkRef(chunk_id=chunk_id, ref=WorkRef(source="default", ref="1")) in refs


@pytest.mark.component
def test_closable_work_refs_excludes_an_unlanded_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ingest(hub, [{"source": "default", "ref": "1"}], promote=True)

    assert hub.services.chunks.closable_work_refs() == []


@pytest.mark.component
def test_closable_work_refs_excludes_a_stopped_chunk_that_never_landed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    chunk = hub.services.chunks.get(chunk_id)
    assert chunk is not None
    hub.services.stop.stop(chunk, by="test")

    assert hub.services.chunks.closable_work_refs() == []


@pytest.mark.component
def test_closable_work_refs_includes_a_landed_chunk_later_stopped(tmp_path: Path) -> None:
    """The plan's own recorded deviation: ``has_landed_repos`` is the sole gate, not
    chunk status — a chunk that landed and was *then* stopped still owes a closure
    attempt, since it was in fact delivered."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    chunk = hub.services.chunks.get(chunk_id)
    assert chunk is not None
    hub.services.stop.stop(chunk, by="test")

    refs = hub.services.chunks.closable_work_refs()

    assert ClosableWorkRef(chunk_id=chunk_id, ref=WorkRef(source="default", ref="1")) in refs


@pytest.mark.component
def test_closable_work_refs_excludes_a_grouped_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    survivor_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=False)
    merged_id = ingest(hub, [{"source": "default", "ref": "2"}], promote=False)
    _land(hub, merged_id)

    hub.services.group.group(survivor_id, [merged_id])

    refs = hub.services.chunks.closable_work_refs()
    assert WorkRef(source="default", ref="2") not in {r.ref for r in refs}


@pytest.mark.component
def test_closable_work_refs_excludes_a_ref_with_a_closed_fact(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    pointer = WorkRef(source="default", ref="1")
    _writable(hub).record_work_item_closure(
        chunk_id, pointer=pointer, outcome=WorkItemCloseOutcome.CLOSED, reason=None, at=hub.clock.now()
    )

    refs = hub.services.chunks.closable_work_refs()

    assert ClosableWorkRef(chunk_id=chunk_id, ref=pointer) not in refs


@pytest.mark.component
def test_closable_work_refs_excludes_a_ref_with_a_gone_fact(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    pointer = WorkRef(source="default", ref="1")
    _writable(hub).record_work_item_closure(
        chunk_id, pointer=pointer, outcome=WorkItemCloseOutcome.GONE, reason="deleted", at=hub.clock.now()
    )

    refs = hub.services.chunks.closable_work_refs()

    assert ClosableWorkRef(chunk_id=chunk_id, ref=pointer) not in refs


@pytest.mark.component
def test_closable_work_refs_still_includes_a_ref_with_only_a_failed_fact(tmp_path: Path) -> None:
    """``failed`` is not terminal — the reconciler retries it on the next sweep."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    pointer = WorkRef(source="default", ref="1")
    _writable(hub).record_work_item_closure(
        chunk_id, pointer=pointer, outcome=WorkItemCloseOutcome.FAILED, reason="boom", at=hub.clock.now()
    )

    refs = hub.services.chunks.closable_work_refs()

    assert ClosableWorkRef(chunk_id=chunk_id, ref=pointer) in refs


# --------------------------------------------------------------------------- #
# IWriteChunkRepository.record_work_item_closure() — the idempotent-bool contract
# --------------------------------------------------------------------------- #


@pytest.mark.component
def test_record_work_item_closure_returns_true_on_the_first_write(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)

    wrote = _writable(hub).record_work_item_closure(
        chunk_id,
        pointer=WorkRef(source="default", ref="1"),
        outcome=WorkItemCloseOutcome.CLOSED,
        reason=None,
        at=hub.clock.now(),
    )

    assert wrote is True


@pytest.mark.component
def test_record_work_item_closure_is_idempotent_per_chunk_source_ref_outcome(tmp_path: Path) -> None:
    """Driven twice, then re-read: the second write is a no-op and returns False —
    the mutation-review re-read (``bzh:mutation-review-selection``)."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    pointer = WorkRef(source="default", ref="1")

    first = _writable(hub).record_work_item_closure(
        chunk_id, pointer=pointer, outcome=WorkItemCloseOutcome.CLOSED, reason=None, at=hub.clock.now()
    )
    second = _writable(hub).record_work_item_closure(
        chunk_id, pointer=pointer, outcome=WorkItemCloseOutcome.CLOSED, reason=None, at=hub.clock.now()
    )

    assert first is True
    assert second is False
    _land(hub, chunk_id)
    assert ClosableWorkRef(chunk_id=chunk_id, ref=pointer) not in hub.services.chunks.closable_work_refs()


@pytest.mark.component
def test_record_work_item_closure_allows_a_distinct_outcome_for_the_same_ref(tmp_path: Path) -> None:
    """A ``failed`` attempt followed by a later ``closed`` one is two distinct rows —
    the unique key is ``(chunk_id, source, ref, outcome)``, not ``(chunk_id, source, ref)``."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    pointer = WorkRef(source="default", ref="1")

    failed = _writable(hub).record_work_item_closure(
        chunk_id, pointer=pointer, outcome=WorkItemCloseOutcome.FAILED, reason="boom", at=hub.clock.now()
    )
    closed = _writable(hub).record_work_item_closure(
        chunk_id, pointer=pointer, outcome=WorkItemCloseOutcome.CLOSED, reason=None, at=hub.clock.now()
    )

    assert failed is True
    assert closed is True


# --------------------------------------------------------------------------- #
# DeliveryClosureReconciler.sweep() — fakes (unit tier)
# --------------------------------------------------------------------------- #


@dataclass
class _RecordedEvent:
    severity: str
    kind: str
    chunk_id: str | None
    message: str
    detail: dict | None


class _FakeClosureChunks:
    """The minimal slice of :class:`IWriteChunkRepository`
    :class:`DeliveryClosureReconciler` calls — ``closable_work_refs`` returns a fixed
    candidate list; ``record_work_item_closure``/``record_event`` are recorded rather
    than persisted, mirroring :class:`~blizzard.hub.work_sources.annotator`-adjacent
    fakes elsewhere in this suite."""

    def __init__(self, candidates: list[ClosableWorkRef]) -> None:
        self._candidates = candidates
        self.closures: list[tuple[str, WorkRef, WorkItemCloseOutcome, str | None]] = []
        self.events: list[_RecordedEvent] = []
        self._written: set[tuple[str, str, str, str]] = set()

    def closable_work_refs(self) -> list[ClosableWorkRef]:
        return list(self._candidates)

    def record_work_item_closure(
        self, chunk_id: str, *, pointer: WorkRef, outcome: WorkItemCloseOutcome, reason: str | None, at: object
    ) -> bool:
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


def _reconciler(chunks: _FakeClosureChunks, closers: dict[str, FakeCloser]) -> DeliveryClosureReconciler:
    registry = WorkSourceRegistry({}, closers=closers)  # type: ignore[arg-type]
    clock = FixedClock(datetime(2026, 8, 1, tzinfo=UTC))
    return DeliveryClosureReconciler(chunks=cast(IWriteChunkRepository, chunks), work_sources=registry, clock=clock)


def test_sweep_closes_a_landed_refs_pointer_and_records_an_info_event() -> None:
    pointer = WorkRef(source="default", ref="1")
    closer = FakeCloser()
    chunks = _FakeClosureChunks([ClosableWorkRef(chunk_id="ch_1", ref=pointer)])

    _reconciler(chunks, {"default": closer}).sweep()

    assert closer.closed == [pointer]
    assert chunks.closures == [("ch_1", pointer, WorkItemCloseOutcome.CLOSED, None)]
    assert len(chunks.events) == 1
    assert chunks.events[0].severity == "info"
    assert chunks.events[0].kind == "work-item-closed"


def test_sweep_closes_each_ref_through_its_own_sources_binding() -> None:
    alpha_ref = WorkRef(source="alpha", ref="1")
    beta_ref = WorkRef(source="beta", ref="2")
    alpha_closer = FakeCloser()
    beta_closer = FakeCloser()
    chunks = _FakeClosureChunks(
        [ClosableWorkRef(chunk_id="ch_1", ref=alpha_ref), ClosableWorkRef(chunk_id="ch_2", ref=beta_ref)]
    )

    _reconciler(chunks, {"alpha": alpha_closer, "beta": beta_closer}).sweep()

    assert alpha_closer.closed == [alpha_ref]
    assert beta_closer.closed == [beta_ref]


def test_sweep_continues_past_one_ref_that_raises() -> None:
    good = WorkRef(source="default", ref="1")
    bad = WorkRef(source="default", ref="2")
    closer = FakeCloser(fail_refs={"2"})
    chunks = _FakeClosureChunks([ClosableWorkRef(chunk_id="ch_1", ref=good), ClosableWorkRef(chunk_id="ch_2", ref=bad)])

    _reconciler(chunks, {"default": closer}).sweep()  # must not raise

    assert closer.closed == [good]
    outcomes = {ref.ref: outcome for _cid, ref, outcome, _reason in chunks.closures}
    assert outcomes["1"] is WorkItemCloseOutcome.CLOSED
    assert outcomes["2"] is WorkItemCloseOutcome.FAILED
    assert {e.kind for e in chunks.events} == {"work-item-closed", "work-item-close-failed"}


def test_sweep_records_a_gone_ref_distinctly_from_a_failed_one() -> None:
    ref = WorkRef(source="default", ref="1")
    closer = FakeCloser(gone_refs={"1"})
    chunks = _FakeClosureChunks([ClosableWorkRef(chunk_id="ch_1", ref=ref)])

    _reconciler(chunks, {"default": closer}).sweep()

    assert chunks.closures == [("ch_1", ref, WorkItemCloseOutcome.GONE, "1 no longer exists")]
    assert chunks.events[0].severity == "warning"
    assert chunks.events[0].kind == "work-item-close-failed"


def test_sweep_skips_a_ref_whose_source_has_no_closer_bound() -> None:
    """A candidate from a source not in ``closing_names()`` is never attempted —
    ``closing_names()`` is the only iteration space."""
    unopted_ref = WorkRef(source="unopted", ref="1")
    chunks = _FakeClosureChunks([ClosableWorkRef(chunk_id="ch_1", ref=unopted_ref)])

    _reconciler(chunks, {}).sweep()

    assert chunks.closures == []
    assert chunks.events == []


def test_sweep_over_no_candidates_does_nothing() -> None:
    """A stopped-and-never-landed chunk contributes nothing to ``closable_work_refs``
    (Phase 3's own concern); this proves the reconciler is a clean no-op over an empty
    candidate set."""
    closer = FakeCloser()

    _reconciler(_FakeClosureChunks([]), {"default": closer}).sweep()

    assert closer.closed == []


# --------------------------------------------------------------------------- #
# DeliveryClosureReconciler.sweep() — real store + FakeCloser (component tier)
# --------------------------------------------------------------------------- #


@pytest.mark.component
def test_sweep_against_a_real_store_is_idempotent_on_a_second_pass(tmp_path: Path) -> None:
    """Driven twice, then re-read: the second pass issues no second close and writes
    no second fact — the mutation-review re-read (``bzh:mutation-review-selection``)."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    closer = FakeCloser()
    registry = WorkSourceRegistry({}, closers={"default": closer})
    reconciler = DeliveryClosureReconciler(chunks=_writable(hub), work_sources=registry, clock=hub.clock)

    reconciler.sweep()
    reconciler.sweep()

    assert closer.closed == [WorkRef(source="default", ref="1")]  # only the first pass actually closed it
    assert hub.services.chunks.closable_work_refs() == []


@pytest.mark.component
def test_sweep_retries_a_failed_ref_on_the_next_pass_until_it_converges(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    _land(hub, chunk_id)
    pointer = WorkRef(source="default", ref="1")
    closer = FakeCloser(fail_refs={"1"})
    registry = WorkSourceRegistry({}, closers={"default": closer})
    reconciler = DeliveryClosureReconciler(chunks=_writable(hub), work_sources=registry, clock=hub.clock)

    reconciler.sweep()
    assert ClosableWorkRef(chunk_id=chunk_id, ref=pointer) in hub.services.chunks.closable_work_refs()

    closer.fail_refs.clear()  # simulate the transient failure clearing before the next sweep
    reconciler.sweep()

    assert ClosableWorkRef(chunk_id=chunk_id, ref=pointer) not in hub.services.chunks.closable_work_refs()
    assert closer.closed == [pointer]
