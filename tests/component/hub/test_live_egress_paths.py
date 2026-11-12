"""A live dataset edit retains the missing path-key diagnostic on every operator surface."""

import threading
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.hub.domain.observability.egress.backfill import BackfillWindowRefused, EgressBackfill
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.hub.domain.observability.egress.event_rows import ExportPaths, export_paths
from blizzard.hub.domain.observability.egress.reset import EgressReset, ResetRefused
from blizzard.hub.domain.observability.egress.status import EgressStatusReader
from blizzard.hub.store.internal.hub_scoped_reads import build_hub_scoped_reads
from tests.support.egress_world import SETTLED, egress_hub, egress_store
from tests.support.hub_event_rows import hub_event_log_of
from tests.support.scaffolding import hub_level_connections
from tests.support.trace_hub import trace_store


def test_live_missing_key_diagnostic(tmp_path: Path) -> None:
    hub, _ = egress_hub(tmp_path, SETTLED)
    config = EgressConfig(directory=tmp_path, datasets=("steps",), file_paths="hashed", path_key_env="MY_KEY")

    def source() -> ExportPaths:
        return export_paths(config, None)

    store = egress_store(hub)
    steps = trace_store(hub)
    reads = build_hub_scoped_reads(hub_level_connections(hub.engine)).corpus
    status = EgressStatusReader(
        config=config,
        rejected=False,
        egress=store,
        event_reads=reads,
        steps=steps,
        clock=hub.clock,
        free_space=lambda: None,
        exported_paths=source,
    )
    reset = EgressReset(
        egress=store,
        events=hub_event_log_of(hub.engine),
        clock=hub.clock,
        config=config,
        active=True,
        pass_lock=threading.Lock(),
        exported_paths=source,
    )
    backfill = EgressBackfill(
        steps=steps,
        egress=store,
        event_reads=reads,
        paths=None,
        clock=hub.clock,
        config=config,
        writers=None,
        exported_paths=source,
    )
    assert status.read().rejected_value is None
    config = replace(config, datasets=("steps", "events"))
    assert status.read().rejected_value == "MY_KEY"
    now = hub.clock.now()
    with pytest.raises(ResetRefused, match="MY_KEY"):
        reset.reset("events", now)
    with pytest.raises(BackfillWindowRefused, match="MY_KEY"):
        backfill.backfill(now - timedelta(seconds=1), now, dataset="events", dry_run=True)
