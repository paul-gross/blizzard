"""The trace export seam for finished span records.

Contract: ``blizzard-product:/delivered/tracing/fleet-spans/spec/emission.md`` §Building spans. The
domain hands :class:`FinishedSpan` batches across; only the binding knows OpenTelemetry."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from blizzard.foundation.trace_spans import FinishedSpan


class ITraceExporter(Protocol):
    def export(self, spans: Sequence[FinishedSpan]) -> bool:
        """Send one batch; ``True`` only when the backend accepted it."""
        ...


class IOwnedTraceExporter(ITraceExporter, Protocol):
    """A trace transport created for, and closed by, its owning scope."""

    def close(self) -> None: ...
