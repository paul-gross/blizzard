"""Finished span records — the output shape both daemons' trace assembly produces.

Contract: ``blizzard-product:/delivered/tracing/fleet-spans/spec/spans.md`` §Spans in a step's trace and
``blizzard-product:/delivered/tracing/runner-spans/spec/spans.md``.
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
    """A link to another span's root; ``context`` is derived, never looked up."""

    context: DerivedContext
    attributes: Attributes = field(default_factory=dict)


@dataclass(frozen=True)
class SpanRecord:
    context: DerivedContext
    #: ``None`` for a trace's root; a child carries its parent's span id.
    parent_span_id: int | None
    name: str
    start: datetime
    end: datetime
    attributes: Attributes
    kind: SpanKind = SpanKind.INTERNAL
    status: SpanStatus = SpanStatus.UNSET
    events: tuple[EventRecord, ...] = ()
    links: tuple[LinkRecord, ...] = ()
    #: Replaces the exporter's resource ``service.name`` for this span alone.
    service_name: str | None = None
