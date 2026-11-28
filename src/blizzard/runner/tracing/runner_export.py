"""The tracing inputs and export handle belonging to one runner."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.runner.tracing.bounded_export import BoundedTraceExporter


@dataclass(frozen=True)
class RunnerTraceOptions:
    """The environment and optional borrowed exporter for a runner's trace sweep."""

    environ: Mapping[str, str]
    injected: ITraceExporter | None


@dataclass
class RunnerTraceExport:
    """The runner's bounded exporter; only an internally built transport is released on close."""

    exporter: BoundedTraceExporter | None
    release: Callable[[], None] | None = None

    def close(self) -> None:
        release, self.release = self.release, None
        if release is not None:
            release()
