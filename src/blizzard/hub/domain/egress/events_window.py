"""The ``events`` dataset's window, shared by the live sweep and the backfill: which markers and drops a pass takes,
and the rows they become.

Contract: ``blizzard-product:/plans/fact-egress/events/spec/export.md`` §What a pass reads. Pure: the store's reads
in, items, positions and rows out. A derivation costs its ``derivation`` row plus one row per event, a drop one row;
a cut never splits a derivation, so one larger than the limit is taken alone."""

from __future__ import annotations

import heapq
from collections.abc import Mapping, Sequence
from datetime import date, datetime

from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.analytics.events import DerivationMarker, DropFact
from blizzard.hub.domain.egress.event_rows import (
    EventDerivation,
    ExportedEventsEntry,
    FilePathPolicy,
    derivation_rows,
    dropped_row,
)
from blizzard.hub.domain.egress.repository import EventsPosition
from blizzard.hub.domain.egress.schema import events_egress_row, partition_of
from blizzard.hub.domain.tracing.facts import StepFacts
from blizzard.hub.egress.writer import EgressValues

_log = get_logger("blizzard.hub.egress")

__all__ = ["EventsItem", "events_rows", "position_of", "take"]

type EventsItem = DerivationMarker | DropFact


def position_of(item: EventsItem) -> EventsPosition:
    if isinstance(item, DropFact):
        return EventsPosition(item.dropped_at, item.segment_id, "")
    return EventsPosition(item.derived_at, item.segment_id, item.extractor_version)


def _cost(item: EventsItem) -> int:
    return 1 if isinstance(item, DropFact) else 1 + item.event_count


def take(
    markers: Sequence[DerivationMarker], drops: Sequence[DropFact], limit: int, *, read_limit: int
) -> tuple[EventsItem, ...]:
    """The items one pass writes, in cursor order, from two reads each capped at ``read_limit``.

    A read that came back full may hold more past its last item, so nothing past that item is taken from the
    other; the rest is cut at ``limit`` rows on an item boundary, always taking at least one item."""
    bounds = [position_of(source[-1]) for source in (markers, drops) if len(source) >= read_limit]
    bound = min(bounds, default=None)
    merged = heapq.merge(markers, drops, key=position_of)
    taken: list[EventsItem] = []
    rows = 0
    for item in merged:
        if bound is not None and position_of(item) > bound:
            break
        cost = _cost(item)
        if taken and rows + cost > limit:
            break
        taken.append(item)
        rows += cost
    return tuple(taken)


def events_rows(
    items: Sequence[EventsItem],
    derivations: Mapping[tuple[str, str], EventDerivation],
    facts: Mapping[str, StepFacts],
    paths: FilePathPolicy,
    now: datetime,
) -> list[tuple[date, EgressValues]]:
    """Each item's rows with their step-start partition, in ``items``' order. A marker absent from ``derivations``
    changed after it was read and is reached again later; an item with no runner step to stand in is skipped."""
    rows: list[tuple[date, EgressValues]] = []
    for item in items:
        try:
            assembled = _assemble(item, derivations, facts, paths, now)
        except (LookupError, ValueError) as error:
            _log.warning("events item has no runner step; not exported", segment_id=item.segment_id, error=str(error))
            continue
        position = position_of(item)
        rows.extend((partition_of(row.step_started_at), events_egress_row(row, position)) for row in assembled)
    return rows


def _assemble(
    item: EventsItem,
    derivations: Mapping[tuple[str, str], EventDerivation],
    facts: Mapping[str, StepFacts],
    paths: FilePathPolicy,
    now: datetime,
) -> tuple[ExportedEventsEntry, ...]:
    if isinstance(item, DropFact):
        return (dropped_row(_facts(facts, item.chunk_id), item, now),)
    derivation = derivations.get((item.segment_id, item.extractor_version))
    if derivation is None:
        return ()
    return derivation_rows(_facts(facts, derivation.chunk_id), derivation, paths, now)


def _facts(facts: Mapping[str, StepFacts], chunk_id: str) -> StepFacts:
    held = facts.get(chunk_id)
    if held is None:
        raise LookupError(f"chunk {chunk_id} has no step facts")
    return held
