"""Finished span records — the domain's own output shape for a step's trace.

Contract: ``blizzard-product:/plans/tracing/fleet-spans/spec/spans.md`` §Spans in a step's trace.
Frozen and free of any OpenTelemetry type; mapping to SDK span data happens at the exporter seam."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from blizzard.foundation.trace_ids import DerivedContext

AttributeValue = str | int | float | bool | tuple[str, ...]
Attributes = Mapping[str, AttributeValue]


class SpanKind(StrEnum):
    INTERNAL = "INTERNAL"


class SpanStatus(StrEnum):
    UNSET = "UNSET"
    ERROR = "ERROR"


@dataclass(frozen=True)
class EventRecord:
    name: str
    time: datetime
    attributes: Attributes = field(default_factory=dict)


@dataclass(frozen=True)
class LinkRecord:
    """A link to the previous step's root; ``context`` is derived, never looked up."""

    context: DerivedContext
    attributes: Attributes = field(default_factory=dict)


@dataclass(frozen=True)
class SpanRecord:
    context: DerivedContext
    #: ``None`` for a step's root; a child carries its root's span id.
    parent_span_id: int | None
    name: str
    start: datetime
    end: datetime
    attributes: Attributes
    kind: SpanKind = SpanKind.INTERNAL
    status: SpanStatus = SpanStatus.UNSET
    events: tuple[EventRecord, ...] = ()
    links: tuple[LinkRecord, ...] = ()
