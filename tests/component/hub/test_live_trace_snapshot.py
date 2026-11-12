"""A running trace pass keeps its settings snapshot across a committed edit."""

from datetime import datetime
from pathlib import Path

import pytest

import blizzard.hub.domain.observability.tracing.sweep as sweep_module
from blizzard.foundation.flags import FlagRegistry
from blizzard.foundation.settings_record.vocabulary import Change
from blizzard.hub.composition import build_hub_settings
from blizzard.hub.config_keys import HUB_REGISTRY
from blizzard.hub.domain.observability.tracing.cursor import CursorKey
from blizzard.hub.domain.observability.tracing.repository import IReadTraceSteps
from blizzard.hub.domain.observability.tracing.sweep import TraceExportSweep
from blizzard.hub.domain.observability.tracing.window import TraceWindow
from blizzard.hub.settings.footing import HubSettingsFooting
from tests.support.hub_event_rows import hub_event_log_of
from tests.support.scaffolding import InMemoryTraceExporter, hub_level_connections
from tests.support.trace_hub import trace_hub, trace_store


def test_committed_edit_during_pass_reaches_only_the_next_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exporter = InMemoryTraceExporter()
    hub, _ = trace_hub(tmp_path, trace_exporter=exporter)
    footing = HubSettingsFooting(tmp_path, None, None, False, FlagRegistry(), False, HUB_REGISTRY, {})
    settings = build_hub_settings(hub_level_connections(hub.engine), hub.clock, footing)
    settings.authoring.apply(Change("edit", sets={"tracing.batch_limit": 3}), if_match=0, door="api", actor="test")
    sweep = TraceExportSweep(
        steps=trace_store(hub),
        exporter=exporter,
        events=hub_event_log_of(hub.engine),
        clock=hub.clock,
        config=lambda: footing.resolve(settings.reader.snapshot().entries).tracing,
    )
    real_read = sweep_module.read_window
    seen: list[int] = []

    def read_with_edit(steps: IReadTraceSteps, cursor: CursorKey, until: datetime, limit: int) -> TraceWindow:
        seen.append(limit)
        if len(seen) == 1:
            settings.authoring.apply(
                Change("edit", sets={"tracing.batch_limit": 7}), if_match=1, door="api", actor="test"
            )
        return real_read(steps, cursor, until, limit)

    monkeypatch.setattr(sweep_module, "read_window", read_with_edit)
    sweep.sweep()  # initialize the export cursor
    sweep.sweep()
    sweep.sweep()
    assert seen == [3, 7]
