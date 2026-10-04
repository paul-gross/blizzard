"""The forge-status projection's decisions — a ref's marker action, the sources that left
annotation, and how the remembered annotating set moves — pinned by value."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.observability.forge_status import AnnotationMemoryChange, departed, marker_action
from blizzard.hub.work_sources.annotator import WorkStatusMarker

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
def test_a_source_departs_when_it_is_remembered_as_annotated_and_is_not_now() -> None:
    assert departed(["b", "a", "c"], ["c"]) == ["a", "b"]
    assert departed([], ["a"]) == []
    assert departed(["a"], ["a"]) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("remembered", "annotating", "unfinished", "entered", "left"),
    [
        ((), ("a",), (), ("a",), ()),
        (("a",), ("a",), (), (), ()),
        (("a", "b"), ("a",), (), (), ("b",)),
        (("a", "b"), ("a",), ("b",), (), ()),
        (("b",), ("c", "a"), (), ("a", "c"), ("b",)),
    ],
)
def test_the_annotation_memory_keeps_every_annotating_source_and_each_unfinished_departure(
    remembered: tuple[str, ...],
    annotating: tuple[str, ...],
    unfinished: tuple[str, ...],
    entered: tuple[str, ...],
    left: tuple[str, ...],
) -> None:
    assert AnnotationMemoryChange.of(remembered, annotating, unfinished) == AnnotationMemoryChange(entered, left)
