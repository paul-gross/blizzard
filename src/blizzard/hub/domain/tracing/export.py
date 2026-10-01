"""The trace export seam — where finished span records leave the hub.

Contract: ``blizzard-product:/plans/tracing/fleet-spans/spec/emission.md`` §Building spans. The
domain hands :class:`SpanRecord` batches across; only the binding knows OpenTelemetry."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from blizzard.hub.domain.tracing.spans import SpanRecord


class ITraceExporter(Protocol):
    def export(self, spans: Sequence[SpanRecord]) -> bool:
        """Send one batch; ``True`` only when the backend accepted it."""
        ...
