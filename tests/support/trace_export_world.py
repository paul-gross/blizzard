"""Helpers for the trace export sweep tests, shared with other test modules."""

from __future__ import annotations

from blizzard.hub.domain.observability.tracing.sweep import TraceExportSweep
from tests.support.scaffolding import (
    HubHarness,
    trace_sweep_of,
)
from tests.support.trace_hub import transitioned_and_stopped


def trace_export_sweep(hub: HubHarness) -> TraceExportSweep:
    sweep = trace_sweep_of(hub)
    assert sweep is not None
    return sweep


def closed_pair(hub: HubHarness, ref: int = 1) -> tuple[str, str]:
    graph = hub.services.graphs.get_enabled_by_name("default-delivery")
    assert graph is not None
    return transitioned_and_stopped(hub, graph, ref)
