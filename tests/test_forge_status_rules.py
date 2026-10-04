"""The forge-status projection's decisions — a ref's marker action and the sources that left
annotation — pinned by value, and the one-shot clear of a source that stops annotating."""

from __future__ import annotations

from typing import cast

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.chunk.ports.work_refs import IReadChunkWorkRefsRepository
from blizzard.hub.domain.observability.forge_status import AnnotationReconciler, departed, marker_action
from blizzard.hub.work_sources.annotator import IWorkAnnotator, WorkStatusMarker
from blizzard.hub.work_sources.source import IWorkSourceRegistry
from tests.support import FakeAnnotator

_INGESTED = WorkStatusMarker.INGESTED
_IN_PROGRESS = WorkStatusMarker.IN_PROGRESS


@pytest.mark.unit
@pytest.mark.parametrize(
    ("desired", "actual", "action"),
    [
        (None, frozenset(), "skip"),
        (None, frozenset({_INGESTED}), "clear"),
        (_INGESTED, frozenset(), "set"),
        (_INGESTED, frozenset({_INGESTED}), "skip"),
        (_INGESTED, frozenset({_IN_PROGRESS}), "set"),
        (_INGESTED, frozenset({_INGESTED, _IN_PROGRESS}), "set"),
    ],
)
def test_a_refs_marker_action(
    desired: WorkStatusMarker | None, actual: frozenset[WorkStatusMarker], action: str
) -> None:
    assert marker_action(desired, actual) == action


@pytest.mark.unit
def test_a_source_departs_when_it_was_annotated_last_pass_and_is_not_now() -> None:
    assert departed(["b", "a", "c"], ["c"]) == ["a", "b"]
    assert departed([], ["a"]) == []
    assert departed(["a"], ["a"]) == []


class _LiveRefs:
    def __init__(self, live: dict[WorkRef, ChunkStatus]) -> None:
        self.live = live

    def live_work_refs(self) -> dict[WorkRef, ChunkStatus]:
        return dict(self.live)


class _Registry:
    def __init__(self, annotators: dict[str, IWorkAnnotator]) -> None:
        self.annotators = annotators

    def annotating_names(self) -> list[str]:
        return list(self.annotators)

    def annotator(self, name: str) -> IWorkAnnotator | None:
        return self.annotators.get(name)


def _reconciler(live: dict[WorkRef, ChunkStatus], registry: _Registry) -> AnnotationReconciler:
    return AnnotationReconciler(
        work_refs=cast(IReadChunkWorkRefsRepository, _LiveRefs(live)),
        work_sources=cast(IWorkSourceRegistry, registry),
    )


@pytest.mark.unit
def test_a_source_that_stops_annotating_has_its_labels_cleared_once() -> None:
    ref = WorkRef(source="default", ref="1")
    annotator = FakeAnnotator()
    registry = _Registry({"default": annotator})
    reconciler = _reconciler({ref: ChunkStatus.READY}, registry)
    reconciler.sweep()
    assert annotator.set_calls == [(ref, _INGESTED)]

    registry.annotators = {}
    reconciler.sweep()
    assert annotator.clear_calls == [ref]

    reconciler.sweep()
    assert annotator.clear_calls == [ref]


@pytest.mark.unit
def test_a_departed_source_whose_clear_failed_is_cleared_on_a_later_pass() -> None:
    ref = WorkRef(source="default", ref="1")
    annotator = FakeAnnotator()
    registry = _Registry({"default": annotator})
    reconciler = _reconciler({ref: ChunkStatus.READY}, registry)
    reconciler.sweep()

    registry.annotators = {}
    annotator.fail_refs = {"1"}
    reconciler.sweep()
    assert annotator.clear_calls == []

    annotator.fail_refs = set()
    reconciler.sweep()
    assert annotator.clear_calls == [ref]
