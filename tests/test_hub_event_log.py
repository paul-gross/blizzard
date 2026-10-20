"""The hub's own event log: its occurrences land in ``hub_event_log`` alone, never in a tenant's ``event_log`` or feed —
written by the hub's start, its migration, and its exports, and read newest-of-a-kind by the exports' latches."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard import __version__
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.hub import app as hub_app
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.domain.observability.hub_events import HubEvent
from blizzard.hub.secrets import ENV_SECRET_KEY
from blizzard.hub.store import schema
from tests.config_inputs import init_hub, load_hub_config, migrate_hub
from tests.hub_event_rows import hub_event_detail, hub_event_kinds, hub_event_log_of, hub_event_rows, hub_events_of
from tests.support import build_hub, hub_scoped

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _tenant_event_kinds(engine: sa.Engine) -> list[str]:
    with engine.connect() as conn:
        return list(conn.execute(hub_scoped(sa.select(schema.event_log.c.kind))).scalars())


def test_a_hub_occurrence_lands_in_the_hub_log_with_its_kinds_severity_and_reaches_no_tenant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    row_id = hub_event_log_of(hub.engine).record(
        kind="egress-write-failed", message="failed", detail={"cause": "disk"}, at=_T0
    )

    [row] = hub_event_rows(hub.engine)
    assert (row.id, row.kind, row.severity, row.message) == (row_id, "egress-write-failed", "warning", "failed")
    assert hub_event_detail(row) == {"cause": "disk"}
    assert _tenant_event_kinds(hub.engine) == []
    resp = hub.client.get("/api/events")
    assert resp.status_code == 200, resp.text
    assert "egress-write-failed" not in resp.text


def test_the_newest_read_takes_the_newest_of_its_kinds_only(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    log = hub_event_log_of(hub.engine)
    events = hub_events_of(hub.engine)
    assert events.newest(("trace-export-failed",)) is None

    log.record(kind="trace-export-failed", message="first", detail=None, at=_T0)
    log.record(kind="trace-export-recovered", message="back", detail=None, at=_T0 + timedelta(seconds=1))
    log.record(kind="hub-started", message="started", detail=None, at=_T0 + timedelta(seconds=2))

    assert events.newest(("trace-export-failed",)) == HubEvent("trace-export-failed", _T0, "first")
    latch = events.newest(("trace-export-failed", "trace-export-recovered"))
    assert latch is not None and latch.kind == "trace-export-recovered"


def test_of_two_kinds_recorded_at_one_instant_the_later_row_is_the_newest(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    log = hub_event_log_of(hub.engine)
    log.record(kind="egress-write-recovered", message="back", detail=None, at=_T0)
    log.record(kind="egress-write-failed", message="again", detail=None, at=_T0)

    newest = hub_events_of(hub.engine).newest(("egress-write-recovered", "egress-write-failed"))

    assert newest is not None and newest.kind == "egress-write-failed"


def test_a_rejected_tracing_setting_is_announced_in_the_hub_log_not_the_tenants(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    hub_app._announce_rejected_tracing(
        TracingSettings(state="rejected", setting="OTEL_EXPORTER_OTLP_PROTOCOL", value="grpc"), hub.hub_services
    )

    assert hub_event_kinds(hub.engine) == ["trace-config-rejected"]
    assert _tenant_event_kinds(hub.engine) == []


def test_each_hosted_start_records_one_hub_started_naming_the_version_and_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_SECRET_KEY, raising=False)
    config = init_hub(tmp_path / "hub")
    head = hub_runtime.migration_runner(config).script_head()

    for _ in range(2):
        app = hub_app.build_hosted_app(load_hub_config(config.root))
        app.state.engine.dispose()

    engine = create_engine_from_url(config.db_url)
    try:
        started = hub_event_rows(engine, "hub-started")
        assert [hub_event_detail(row) for row in started] == [{"version": __version__, "revision": head}] * 2
        assert {row.severity for row in started} == {"info"}
        assert _tenant_event_kinds(engine) == []
    finally:
        engine.dispose()


def test_a_migrate_with_nothing_to_apply_records_no_hub_migrated(tmp_path: Path) -> None:
    config = init_hub(tmp_path)

    migrate_hub(tmp_path)

    engine = create_engine_from_url(config.db_url)
    try:
        assert hub_event_rows(engine, "hub-migrated") == []
    finally:
        engine.dispose()


def test_a_migrate_from_an_older_store_records_the_revisions_it_moved_between(tmp_path: Path) -> None:
    config = init_hub(tmp_path)
    runner = hub_runtime.migration_runner(config)
    head = runner.script_head()
    runner.downgrade("20261008_1500_scope_surrogate")

    migrate_hub(tmp_path)

    engine = create_engine_from_url(config.db_url)
    try:
        [migrated] = hub_event_rows(engine, "hub-migrated")
        assert hub_event_detail(migrated) == {"from": "20261008_1500_scope_surrogate", "to": head}
        assert migrated.message == f"hub store migrated from revision 20261008_1500_scope_surrogate to {head}"
    finally:
        engine.dispose()


def test_a_downgrade_records_no_hub_migrated(tmp_path: Path) -> None:
    config = init_hub(tmp_path)

    migrate_hub(tmp_path, down="20261008_1600_hub_event_log_move")

    engine = create_engine_from_url(config.db_url)
    try:
        assert hub_event_rows(engine, "hub-migrated") == []
    finally:
        engine.dispose()
