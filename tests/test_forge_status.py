"""The forge-status projection — derivation and the reconciler.

``WorkStatusMarker.of`` is a pure, exhaustive derivation (unit tier); ``live_work_refs()`` and
``AnnotationReconciler.sweep()`` are exercised against a real, migrated chunk store with a
:class:`FakeAnnotator` standing in for the forge (component
tier), not the HTTP shaping ``tests/test_work_source.py`` already covers."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.observability.forge_status import AnnotationReconciler
from blizzard.hub.store.internal.forge_annotation_store import ForgeAnnotationStore
from blizzard.hub.work_sources.annotator import IWorkAnnotator, WorkAnnotateError, WorkStatusMarker
from tests.support import (
    FakeAnnotator,
    FakeWorkSource,
    HubHarness,
    WorkSourceRegistry,
    build_hub,
    count_queries,
    hub_store_connections,
    ingest,
)

# --- WorkStatusMarker.of — pure, exhaustive over ChunkStatus ---

pytestmark = pytest.mark.unit


def test_marker_of_is_exhaustive_over_chunk_status() -> None:
    """Fails the moment a new `ChunkStatus` member is added and left unmapped."""
    for status in ChunkStatus:
        WorkStatusMarker.of(status)


@pytest.mark.parametrize("status", [ChunkStatus.NOT_READY, ChunkStatus.READY])
def test_marker_of_maps_unclaimed_statuses_to_ingested(status: ChunkStatus) -> None:
    assert WorkStatusMarker.of(status) is WorkStatusMarker.INGESTED


@pytest.mark.parametrize(
    "status",
    [
        ChunkStatus.RUNNING,
        ChunkStatus.PAUSED,
        ChunkStatus.WAITING_ON_HUMAN,
        ChunkStatus.NEEDS_HUMAN,
        ChunkStatus.DELIVERING,
    ],
)
def test_marker_of_maps_live_statuses_to_in_progress(status: ChunkStatus) -> None:
    assert WorkStatusMarker.of(status) is WorkStatusMarker.IN_PROGRESS


@pytest.mark.parametrize("status", [ChunkStatus.STOPPED, ChunkStatus.DONE])
def test_marker_of_maps_terminal_statuses_to_none(status: ChunkStatus) -> None:
    assert WorkStatusMarker.of(status) is None


# --- IReadChunkWorkRefsRepository.live_work_refs() — real store, real migrations ---


@pytest.mark.component
def test_live_work_refs_includes_not_ready_and_ready_chunks(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    ingest(hub, [{"source": "default", "ref": "1"}], promote=False)
    ingest(hub, [{"source": "default", "ref": "2"}], promote=True)

    refs = hub.services.chunks.work_refs.live_work_refs()

    assert refs[WorkRef(source="default", ref="1")] is ChunkStatus.NOT_READY
    assert refs[WorkRef(source="default", ref="2")] is ChunkStatus.READY


@pytest.mark.component
def test_live_work_refs_excludes_a_terminal_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    hub.services.stop.stop(chunk, by="test")

    refs = hub.services.chunks.work_refs.live_work_refs()

    assert WorkRef(source="default", ref="1") not in refs


@pytest.mark.component
def test_live_work_refs_excludes_a_grouped_chunk_but_carries_its_ref_via_the_survivor(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    survivor_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=False)
    merged_id = ingest(hub, [{"source": "default", "ref": "2"}], promote=False)

    hub.services.group.group(survivor_id, [merged_id])
    refs = hub.services.chunks.work_refs.live_work_refs()

    assert refs[WorkRef(source="default", ref="1")] is ChunkStatus.NOT_READY
    assert refs[WorkRef(source="default", ref="2")] is ChunkStatus.NOT_READY  # via the survivor now


@pytest.mark.component
def test_live_work_refs_statement_count_does_not_grow_with_work_ref_count(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    for ref in ("1", "2", "3"):
        ingest(hub, [{"source": "default", "ref": ref}], promote=True)
    few_count = count_queries(hub.engine, lambda: hub.services.chunks.work_refs.live_work_refs())

    for ref in ("4", "5", "6", "7", "8", "9", "10", "11", "12"):
        ingest(hub, [{"source": "default", "ref": ref}], promote=True)
    many_count = count_queries(hub.engine, lambda: hub.services.chunks.work_refs.live_work_refs())

    assert few_count == many_count


# --- AnnotationReconciler.sweep() — real store, FakeAnnotator standing in for the forge ---


_FLOOR = timedelta(minutes=10)


def _reconciler(hub: HubHarness, work_sources: WorkSourceRegistry) -> AnnotationReconciler:
    """A reconciler over ``hub``'s own store — a fresh one per call, as a restart builds."""
    return AnnotationReconciler(
        work_refs=hub.services.chunks.work_refs,
        work_sources=work_sources,
        memory=ForgeAnnotationStore(hub_store_connections(hub.engine)),
        clock=hub.clock,
        full_pass_floor=_FLOOR,
    )


@pytest.mark.component
def test_sweep_makes_zero_write_calls_for_an_already_correct_ref(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    ingest(hub, [{"source": "default", "ref": "1"}], promote=True)  # ready -> ingested
    annotator = FakeAnnotator(initial={WorkRef(source="default", ref="1"): {WorkStatusMarker.INGESTED}})
    reconciler = _reconciler(hub, WorkSourceRegistry({}, {"default": annotator}))

    reconciler.sweep()

    assert annotator.set_calls == []
    assert annotator.clear_calls == []


@pytest.mark.component
def test_sweep_corrects_a_doubly_marked_ref_to_the_one_desired_marker(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    ingest(hub, [{"source": "default", "ref": "1"}], promote=True)  # ready -> ingested
    annotator = FakeAnnotator(
        initial={WorkRef(source="default", ref="1"): {WorkStatusMarker.INGESTED, WorkStatusMarker.IN_PROGRESS}}
    )
    reconciler = _reconciler(hub, WorkSourceRegistry({}, {"default": annotator}))

    reconciler.sweep()

    assert annotator.set_calls == [(WorkRef(source="default", ref="1"), WorkStatusMarker.INGESTED)]


@pytest.mark.component
def test_sweep_clears_a_ref_the_hub_no_longer_holds(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    annotator = FakeAnnotator(initial={WorkRef(source="default", ref="999"): {WorkStatusMarker.INGESTED}})
    reconciler = _reconciler(hub, WorkSourceRegistry({}, {"default": annotator}))

    reconciler.sweep()

    assert annotator.clear_calls == [WorkRef(source="default", ref="999")]


@pytest.mark.component
def test_sweep_clears_a_stopped_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    hub.services.stop.stop(chunk, by="test")
    annotator = FakeAnnotator(initial={WorkRef(source="default", ref="1"): {WorkStatusMarker.INGESTED}})
    reconciler = _reconciler(hub, WorkSourceRegistry({}, {"default": annotator}))

    reconciler.sweep()

    assert annotator.clear_calls == [WorkRef(source="default", ref="1")]


@pytest.mark.component
def test_sweep_scopes_each_annotator_to_its_own_source(tmp_path: Path) -> None:
    """A ref belonging to another source is never passed to this annotator."""
    hub = build_hub(
        tmp_path,
        work_sources={"default": FakeWorkSource(name="default"), "other": FakeWorkSource(name="other")},
    )
    ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    ingest(hub, [{"source": "other", "ref": "2"}], promote=True)
    default_annotator = FakeAnnotator()
    other_annotator = FakeAnnotator()
    reconciler = _reconciler(hub, WorkSourceRegistry({}, {"default": default_annotator, "other": other_annotator}))

    reconciler.sweep()

    assert default_annotator.set_calls == [(WorkRef(source="default", ref="1"), WorkStatusMarker.INGESTED)]
    assert other_annotator.set_calls == [(WorkRef(source="other", ref="2"), WorkStatusMarker.INGESTED)]


@pytest.mark.component
def test_sweep_continues_past_a_failing_ref(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    ingest(hub, [{"source": "default", "ref": "2"}], promote=True)
    annotator = FakeAnnotator(fail_refs={"1"})
    reconciler = _reconciler(hub, WorkSourceRegistry({}, {"default": annotator}))

    reconciler.sweep()  # must not raise

    assert (WorkRef(source="default", ref="2"), WorkStatusMarker.INGESTED) in annotator.set_calls
    assert all(ref.ref != "1" for ref, _ in annotator.set_calls)


@pytest.mark.component
def test_sweep_reconverges_after_a_simulated_mid_sweep_crash(tmp_path: Path) -> None:
    """No hub-side record of a label write exists, so a fresh sweep re-diffs from
    scratch — a prior sweep truncated after writing only one of two refs still
    reaches the fully converged state on the next call."""
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    ingest(hub, [{"source": "default", "ref": "2"}], promote=True)
    annotator = FakeAnnotator()
    reconciler = _reconciler(hub, WorkSourceRegistry({}, {"default": annotator}))

    # Simulate a sweep truncated right after ref 1 landed on the forge.
    annotator.set_status(WorkRef(source="default", ref="1"), WorkStatusMarker.INGESTED)
    annotator.set_calls.clear()

    reconciler.sweep()

    assert annotator.marked_refs() == {
        WorkRef(source="default", ref="1"): frozenset({WorkStatusMarker.INGESTED}),
        WorkRef(source="default", ref="2"): frozenset({WorkStatusMarker.INGESTED}),
    }


# --- A source leaving annotation across a restart ---


@pytest.mark.component
def test_a_source_that_stops_annotating_across_a_restart_has_its_labels_cleared_once(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    ref = WorkRef(source="default", ref="1")
    annotator = FakeAnnotator()
    _reconciler(hub, WorkSourceRegistry({}, {"default": annotator})).sweep()
    assert annotator.set_calls == [(ref, WorkStatusMarker.INGESTED)]

    # The restart: a fresh reconciler, `annotate` now off — the binding stays configured.
    restarted = _reconciler(hub, WorkSourceRegistry({}, {}, label_clearers={"default": annotator}))
    restarted.sweep()
    assert annotator.clear_calls == [ref]

    restarted.sweep()
    _reconciler(hub, WorkSourceRegistry({}, {}, label_clearers={"default": annotator})).sweep()
    assert annotator.clear_calls == [ref]


@pytest.mark.component
def test_a_departed_source_whose_clear_failed_is_cleared_after_another_restart(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    ref = WorkRef(source="default", ref="1")
    annotator = FakeAnnotator()
    _reconciler(hub, WorkSourceRegistry({}, {"default": annotator})).sweep()

    annotator.fail_refs = {"1"}
    _reconciler(hub, WorkSourceRegistry({}, {}, label_clearers={"default": annotator})).sweep()
    assert annotator.clear_calls == []

    annotator.fail_refs = set()
    _reconciler(hub, WorkSourceRegistry({}, {}, label_clearers={"default": annotator})).sweep()
    assert annotator.clear_calls == [ref]


@pytest.mark.component
def test_a_hub_that_never_annotated_clears_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    labelled = FakeAnnotator(initial={WorkRef(source="default", ref="1"): {WorkStatusMarker.INGESTED}})

    _reconciler(hub, WorkSourceRegistry({}, {}, label_clearers={"default": labelled})).sweep()

    assert labelled.clear_calls == []
    assert labelled.set_calls == []


@pytest.mark.component
def test_a_source_removed_from_config_is_forgotten_without_a_clear(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    annotator = FakeAnnotator()
    _reconciler(hub, WorkSourceRegistry({}, {"default": annotator})).sweep()
    memory = ForgeAnnotationStore(hub_store_connections(hub.engine))
    assert memory.annotated_sources() == {"default"}

    _reconciler(hub, WorkSourceRegistry({}, {})).sweep()

    assert memory.annotated_sources() == frozenset()


# --- The probe gate ---


def _gated(tmp_path: Path) -> tuple[HubHarness, FakeAnnotator, AnnotationReconciler]:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    annotator = FakeAnnotator()
    return hub, annotator, _reconciler(hub, WorkSourceRegistry({}, {"default": annotator}))


@pytest.mark.component
def test_a_sweep_with_unchanged_facts_before_the_floor_reads_neither_the_forge_nor_the_work_refs(
    tmp_path: Path,
) -> None:
    hub, annotator, reconciler = _gated(tmp_path)
    reconciler.sweep()
    assert annotator.read_calls == 1

    hub.clock.advance(_FLOOR - timedelta(seconds=1))
    live_reads = count_queries(hub.engine, reconciler.sweep)

    assert annotator.read_calls == 1
    assert live_reads == 1  # the signature statement alone


@pytest.mark.component
def test_a_new_work_ref_or_status_fact_runs_a_full_pass(tmp_path: Path) -> None:
    hub, annotator, reconciler = _gated(tmp_path)
    reconciler.sweep()

    ingest(hub, [{"source": "default", "ref": "2"}], promote=True)
    reconciler.sweep()
    assert annotator.read_calls == 2

    chunk_id = ingest(hub, [{"source": "default", "ref": "3"}], promote=False)
    reconciler.sweep()
    assert annotator.read_calls == 3

    hub.client.post(f"/api/chunks/{chunk_id}/promote")
    reconciler.sweep()
    assert annotator.read_calls == 4


@pytest.mark.component
def test_a_change_to_the_annotating_set_runs_a_full_pass(tmp_path: Path) -> None:
    hub, annotator, _ = _gated(tmp_path)
    other = FakeAnnotator()
    registry = _GrowingRegistry({"default": annotator}, joiner=("other", other))
    reconciler = _reconciler(hub, registry)
    reconciler.sweep()

    registry.joined = True
    reconciler.sweep()

    assert other.read_calls == 1
    assert annotator.read_calls == 2


@pytest.mark.component
def test_the_floor_elapsing_runs_a_full_pass_and_restarts_the_floor(tmp_path: Path) -> None:
    hub, annotator, reconciler = _gated(tmp_path)
    reconciler.sweep()
    hub.clock.advance(_FLOOR)
    reconciler.sweep()
    assert annotator.read_calls == 2

    hub.clock.advance(_FLOOR - timedelta(seconds=1))
    reconciler.sweep()
    assert annotator.read_calls == 2


@pytest.mark.component
def test_a_hand_removed_label_is_re_asserted_by_the_floor_pass_not_a_skipped_one(tmp_path: Path) -> None:
    hub, annotator, reconciler = _gated(tmp_path)
    ref = WorkRef(source="default", ref="1")
    reconciler.sweep()
    annotator.clear_status(ref)

    reconciler.sweep()
    assert annotator.marked_refs() == {}
    hub.clock.advance(_FLOOR)
    reconciler.sweep()

    assert annotator.marked_refs() == {ref: frozenset({WorkStatusMarker.INGESTED})}


@pytest.mark.component
def test_a_failed_write_is_retried_on_the_next_sweep_not_deferred_for_a_floor(tmp_path: Path) -> None:
    _, annotator, reconciler = _gated(tmp_path)
    annotator.fail_refs = {"1"}
    reconciler.sweep()

    annotator.fail_refs = set()
    reconciler.sweep()

    assert annotator.set_calls == [(WorkRef(source="default", ref="1"), WorkStatusMarker.INGESTED)]


@pytest.mark.component
def test_an_unreadable_source_is_retried_on_the_next_sweep(tmp_path: Path) -> None:
    hub, _, _ = _gated(tmp_path)
    annotator = _FlakyRead()
    reconciler = _reconciler(hub, WorkSourceRegistry({}, {"default": annotator}))
    reconciler.sweep()

    annotator.unreadable = False
    reconciler.sweep()

    assert annotator.set_calls == [(WorkRef(source="default", ref="1"), WorkStatusMarker.INGESTED)]


@pytest.mark.component
def test_an_unfinished_departure_is_retried_on_the_next_sweep(tmp_path: Path) -> None:
    hub, annotator, _ = _gated(tmp_path)
    _reconciler(hub, WorkSourceRegistry({}, {"default": annotator})).sweep()
    annotator.fail_refs = {"1"}
    restarted = _reconciler(hub, WorkSourceRegistry({}, {}, label_clearers={"default": annotator}))
    restarted.sweep()

    annotator.fail_refs = set()
    restarted.sweep()

    assert annotator.clear_calls == [WorkRef(source="default", ref="1")]


class _GrowingRegistry(WorkSourceRegistry):
    """A registry whose annotating set gains ``joiner`` once ``joined`` is set."""

    def __init__(self, annotators: dict[str, FakeAnnotator], *, joiner: tuple[str, FakeAnnotator]) -> None:
        super().__init__({}, annotators)
        self._joiner = joiner
        self.joined = False

    def annotating_names(self) -> list[str]:
        return [*super().annotating_names(), *([self._joiner[0]] if self.joined else [])]

    def annotator(self, name: str) -> FakeAnnotator | IWorkAnnotator | None:
        return self._joiner[1] if self.joined and name == self._joiner[0] else super().annotator(name)


class _FlakyRead(FakeAnnotator):
    unreadable = True

    def marked_refs(self) -> dict[WorkRef, frozenset[WorkStatusMarker]]:
        if self.unreadable:
            raise WorkAnnotateError("forge unreachable")
        return super().marked_refs()


class _RememberedAtWrite(FakeAnnotator):
    """Records what the memory held at each label write."""

    def __init__(self, memory: ForgeAnnotationStore) -> None:
        super().__init__()
        self._memory = memory
        self.remembered_at_write: list[frozenset[str]] = []
        self.remembered_at_clear: list[frozenset[str]] = []

    def set_status(self, pointer: WorkRef, marker: WorkStatusMarker) -> None:
        self.remembered_at_write.append(self._memory.annotated_sources())
        super().set_status(pointer, marker)

    def clear_status(self, pointer: WorkRef) -> None:
        self.remembered_at_clear.append(self._memory.annotated_sources())
        super().clear_status(pointer)


@pytest.mark.component
def test_an_entering_source_is_remembered_before_its_first_label_write_and_forgotten_only_after_its_clear(
    tmp_path: Path,
) -> None:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    memory = ForgeAnnotationStore(hub_store_connections(hub.engine))
    annotator = _RememberedAtWrite(memory)

    _reconciler(hub, WorkSourceRegistry({}, {"default": annotator})).sweep()
    assert annotator.remembered_at_write == [frozenset({"default"})]

    _reconciler(hub, WorkSourceRegistry({}, {}, label_clearers={"default": annotator})).sweep()
    assert annotator.remembered_at_clear == [frozenset({"default"})]
    assert memory.annotated_sources() == frozenset()


# --- IReadChunkWorkRefsRepository.live_work_refs_signature() ---


@pytest.mark.component
def test_the_work_refs_signature_moves_on_every_input_and_answers_in_one_statement(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    work_refs = hub.services.chunks.work_refs
    empty = work_refs.live_work_refs_signature()
    assert count_queries(hub.engine, work_refs.live_work_refs_signature) == 1

    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=False)
    minted = work_refs.live_work_refs_signature()
    hub.client.post(f"/api/chunks/{chunk_id}/promote")
    promoted = work_refs.live_work_refs_signature()
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    hub.services.stop.stop(chunk, by="test")
    stopped = work_refs.live_work_refs_signature()

    assert len({empty, minted, promoted, stopped}) == 4
    assert work_refs.live_work_refs_signature() == stopped
