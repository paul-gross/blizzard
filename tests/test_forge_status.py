"""The forge-status projection (issue #179) — derivation and the reconciler.

``WorkStatusMarker.of`` is a pure, exhaustive derivation (unit tier); ``live_work_refs()`` and
``AnnotationReconciler.sweep()`` are exercised against a real, migrated chunk store with a
:class:`FakeAnnotator` standing in for the forge (component
tier), not the HTTP shaping ``tests/test_work_source.py`` already covers."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.forge_status import AnnotationReconciler
from blizzard.hub.domain.work import WorkRef
from blizzard.hub.work_sources.annotator import WorkStatusMarker
from blizzard.hub.work_sources.registry import WorkSourceRegistry
from tests.support import FakeAnnotator, FakeWorkSource, build_hub, ingest

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


# --- AnnotationReconciler.sweep() — real store, FakeAnnotator standing in for the forge ---


@pytest.mark.component
def test_sweep_makes_zero_write_calls_for_an_already_correct_ref(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    ingest(hub, [{"source": "default", "ref": "1"}], promote=True)  # ready -> ingested
    annotator = FakeAnnotator(initial={WorkRef(source="default", ref="1"): {WorkStatusMarker.INGESTED}})
    reconciler = AnnotationReconciler(
        work_refs=hub.services.chunks.work_refs, work_sources=WorkSourceRegistry({}, {"default": annotator})
    )

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
    reconciler = AnnotationReconciler(
        work_refs=hub.services.chunks.work_refs, work_sources=WorkSourceRegistry({}, {"default": annotator})
    )

    reconciler.sweep()

    assert annotator.set_calls == [(WorkRef(source="default", ref="1"), WorkStatusMarker.INGESTED)]


@pytest.mark.component
def test_sweep_clears_a_ref_the_hub_no_longer_holds(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    annotator = FakeAnnotator(initial={WorkRef(source="default", ref="999"): {WorkStatusMarker.INGESTED}})
    reconciler = AnnotationReconciler(
        work_refs=hub.services.chunks.work_refs, work_sources=WorkSourceRegistry({}, {"default": annotator})
    )

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
    reconciler = AnnotationReconciler(
        work_refs=hub.services.chunks.work_refs, work_sources=WorkSourceRegistry({}, {"default": annotator})
    )

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
    reconciler = AnnotationReconciler(
        work_refs=hub.services.chunks.work_refs,
        work_sources=WorkSourceRegistry({}, {"default": default_annotator, "other": other_annotator}),
    )

    reconciler.sweep()

    assert default_annotator.set_calls == [(WorkRef(source="default", ref="1"), WorkStatusMarker.INGESTED)]
    assert other_annotator.set_calls == [(WorkRef(source="other", ref="2"), WorkStatusMarker.INGESTED)]


@pytest.mark.component
def test_sweep_continues_past_a_failing_ref(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    ingest(hub, [{"source": "default", "ref": "2"}], promote=True)
    annotator = FakeAnnotator(fail_refs={"1"})
    reconciler = AnnotationReconciler(
        work_refs=hub.services.chunks.work_refs, work_sources=WorkSourceRegistry({}, {"default": annotator})
    )

    reconciler.sweep()  # must not raise

    assert (WorkRef(source="default", ref="2"), WorkStatusMarker.INGESTED) in annotator.set_calls
    assert all(ref.ref != "1" for ref, _ in annotator.set_calls)


@pytest.mark.component
def test_sweep_reconverges_after_a_simulated_mid_sweep_crash(tmp_path: Path) -> None:
    """No hub-side annotation state exists, so a fresh sweep re-diffs from
    scratch — a prior sweep truncated after writing only one of two refs still
    reaches the fully converged state on the next call."""
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(name="default")})
    ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    ingest(hub, [{"source": "default", "ref": "2"}], promote=True)
    annotator = FakeAnnotator()
    reconciler = AnnotationReconciler(
        work_refs=hub.services.chunks.work_refs, work_sources=WorkSourceRegistry({}, {"default": annotator})
    )

    # Simulate a sweep truncated right after ref 1 landed on the forge.
    annotator.set_status(WorkRef(source="default", ref="1"), WorkStatusMarker.INGESTED)
    annotator.set_calls.clear()

    reconciler.sweep()

    assert annotator.marked_refs() == {
        WorkRef(source="default", ref="1"): frozenset({WorkStatusMarker.INGESTED}),
        WorkRef(source="default", ref="2"): frozenset({WorkStatusMarker.INGESTED}),
    }
