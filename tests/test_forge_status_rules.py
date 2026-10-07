"""The forge-status projection's decisions — a ref's marker action, the sources that left
annotation, and how the remembered annotating set moves — pinned by value."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.domain.chunk.ports.work_refs import WorkRefsSignature
from blizzard.hub.domain.observability.forge_status import (
    AnnotationMemoryChange,
    AnnotationProbe,
    annotation_due,
    departed,
    marker_action,
)
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
    ("remembered", "annotating", "entered"),
    [
        ((), ("a",), ("a",)),
        (("a",), ("a",), ()),
        (("a", "b"), ("a",), ()),
        (("b",), ("c", "a"), ("a", "c")),
    ],
)
def test_an_entering_source_is_remembered_before_its_first_write(
    remembered: tuple[str, ...], annotating: tuple[str, ...], entered: tuple[str, ...]
) -> None:
    assert AnnotationMemoryChange.on_entry(remembered, annotating) == AnnotationMemoryChange(entered=entered)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("leaving", "unfinished", "left"),
    [
        ((), (), ()),
        (("b", "a"), (), ("a", "b")),
        (("a", "b"), ("b",), ("a",)),
        (("a",), ("a",), ()),
    ],
)
def test_a_departed_source_is_forgotten_only_once_its_clear_finishes(
    leaving: tuple[str, ...], unfinished: tuple[str, ...], left: tuple[str, ...]
) -> None:
    assert AnnotationMemoryChange.on_departure(leaving, unfinished) == AnnotationMemoryChange(left=left)


_FLOOR = timedelta(minutes=10)
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _probe(marks: int = 1, annotating: tuple[str, ...] = ("a",)) -> AnnotationProbe:
    return AnnotationProbe(
        work_refs=WorkRefsSignature(marks=(("chunks", marks, None),)), annotating=frozenset(annotating)
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("last_probe", "probe", "last_pass", "now", "due"),
    [
        (None, _probe(), None, _NOW, True),  # a fresh reconciler has converged no pass
        (_probe(), _probe(), _NOW, _NOW + timedelta(seconds=1), False),
        (_probe(), _probe(marks=2), _NOW, _NOW + timedelta(seconds=1), True),  # a fact or work-ref insert
        (_probe(), _probe(annotating=("a", "b")), _NOW, _NOW + timedelta(seconds=1), True),  # the set moved
        (_probe(), _probe(), _NOW, _NOW + _FLOOR - timedelta(seconds=1), False),
        (_probe(), _probe(), _NOW, _NOW + _FLOOR, True),  # the floor elapsed
    ],
)
def test_a_pass_is_due_when_the_probe_moved_the_floor_elapsed_or_none_has_converged(
    last_probe: AnnotationProbe | None, probe: AnnotationProbe, last_pass: datetime | None, now: datetime, due: bool
) -> None:
    assert annotation_due(last_probe, probe, last_pass, now, _FLOOR) is due
