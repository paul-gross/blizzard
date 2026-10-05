"""``POST /api/egress/backfill`` and ``blizzard hub egress backfill`` (component tier) — a past window written
through the live sweep's own row assembly, with the cursors untouched."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.cli import hub as hub_group
from blizzard.hub.config import EgressConfig
from blizzard.hub.domain.observability.analytics.extraction import EXTRACTOR_VERSION
from blizzard.hub.domain.observability.egress.backfill import EgressBackfill
from blizzard.hub.domain.observability.egress.event_rows import FilePathPolicy
from blizzard.hub.egress.writer import EgressFailure, EgressFailureCause
from blizzard.hub.store.internal.egress_event_store import EgressEventStore
from blizzard.hub.store.internal.trace_store import TraceStore
from tests.support import HubHarness, InMemoryEgressWriter, hub_store_connections
from tests.test_egress_events_sweep import _OLD_VERSION, _Events
from tests.test_egress_sweep import _SETTLED, _closed_step, _cursor_rows, _hub, _kinds, _rows, _store, _sweep
from tests.trace_hub import label

pytestmark = pytest.mark.component

_ENV = {"BZ_HUB_URL": "http://hub.local:8421"}


def _real(tmp_path: Path, **extra: object) -> EgressConfig:
    (tmp_path / "out").mkdir(exist_ok=True)
    return EgressConfig(directory=tmp_path / "out", settle_seconds=0, min_free_bytes=0, **extra)  # type: ignore[arg-type]


def _backfill(hub: HubHarness, since: datetime, until: datetime, **extra: object) -> httpx.Response:
    body = {"since": iso_utc(since), "until": iso_utc(until), **extra}
    return hub.client.post("/api/egress/backfill", json=body)


def _service(hub: HubHarness, config: EgressConfig, writer: InMemoryEgressWriter) -> EgressBackfill:
    connections = hub_store_connections(hub.engine)
    return EgressBackfill(
        steps=TraceStore(connections, graphs=hub.services.graphs, label=label),
        egress=_store(hub),
        event_reads=EgressEventStore(connections),
        paths=FilePathPolicy("absolute"),
        clock=hub.clock,
        config=config,
        writers=lambda: writer,
    )


def _without_export_time(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(({k: v for k, v in row.items() if k != "exported_at"} for row in rows), key=repr)


def _data_files(directory: Path) -> list[Path]:
    return sorted(
        p for p in directory.rglob("*") if p.is_file() and "_manifests" not in p.parts and "_schema" not in p.parts
    )


def test_the_backfill_writes_the_live_sweeps_rows_for_the_same_window(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path, _SETTLED)
    live = InMemoryEgressWriter()
    _sweep(hub, live).sweep()
    since = hub.clock.now()
    _closed_step(hub, graph, 1, usage=2)
    _closed_step(hub, graph, 2, usage=1)
    hub.clock.advance(timedelta(seconds=1))
    _sweep(hub, live).sweep()
    until = hub.clock.now() + timedelta(seconds=1)
    hub.clock.advance(timedelta(seconds=1))
    assert len(_rows(live, "steps")) == 2
    assert len(_rows(live, "invocations")) == 3

    written = InMemoryEgressWriter()
    result = _service(hub, _SETTLED, written).backfill(since, until, dataset=None, dry_run=False)

    assert _without_export_time(_rows(written, "steps")) == _without_export_time(_rows(live, "steps"))
    assert _without_export_time(_rows(written, "invocations")) == _without_export_time(_rows(live, "invocations"))
    assert [(c.dataset, c.rows) for c in result.datasets] == [("steps", 2), ("invocations", 3), ("events", 0)]
    assert all(p.backfill for p, _ in written.manifests)


def test_the_window_is_half_open(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path, _SETTLED)
    _sweep(hub, InMemoryEgressWriter()).sweep()
    since = hub.clock.now()
    _closed_step(hub, graph, 1)  # closes 5s in
    closed_at = hub.clock.now()
    hub.clock.advance(timedelta(seconds=1))
    written = InMemoryEgressWriter()
    service = _service(hub, _SETTLED, written)

    service.backfill(since, closed_at, dataset="steps", dry_run=False)
    assert _rows(written, "steps") == []

    service.backfill(since, closed_at + timedelta(microseconds=1), dataset="steps", dry_run=False)
    assert len(_rows(written, "steps")) == 1


def test_a_real_directory_holds_backfill_files_per_date_partition_and_the_cursors_stay(tmp_path: Path) -> None:
    config = _real(tmp_path)
    hub, graph = _hub(tmp_path, config)
    sweep = hub.services.egress_export
    assert sweep is not None
    sweep.sweep()
    since = hub.clock.now()
    _closed_step(hub, graph, 1)
    hub.clock.advance(timedelta(days=1))
    _closed_step(hub, graph, 2)
    cursors = _cursor_rows(hub)
    newest = _store(hub).newest_cursor("steps")
    until = hub.clock.now() + timedelta(seconds=1)
    hub.clock.advance(timedelta(seconds=1))

    resp = _backfill(hub, since, until, dataset="steps")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"dry_run": False, "datasets": [{"dataset": "steps", "rows": 2, "files": 2}]}
    directory = tmp_path / "out"
    files = _data_files(directory)
    assert len(files) == 2
    assert all(f.name.endswith("-backfill.ndjson.gz") for f in files)
    assert sorted(f.parent.name for f in files) == ["date=2026-07-13", "date=2026-07-14"]
    manifests = [json.loads(m.read_text()) for m in (directory / "_manifests").glob("*.json")]
    assert [m["backfill"] for m in manifests] == [True]  # one page, one pass, one manifest
    assert _cursor_rows(hub) == cursors
    assert _store(hub).newest_cursor("steps") == newest


def test_a_dry_run_writes_nothing_and_counts_what_a_wet_run_counts(tmp_path: Path) -> None:
    config = _real(tmp_path, max_rows_per_file=2)
    hub, graph = _hub(tmp_path, config)
    sweep = hub.services.egress_export
    assert sweep is not None
    sweep.sweep()
    since = hub.clock.now()
    for ref in (1, 2, 3):
        _closed_step(hub, graph, ref, usage=1)
    until = hub.clock.now() + timedelta(seconds=1)
    hub.clock.advance(timedelta(seconds=1))
    directory = tmp_path / "out"

    dry = _backfill(hub, since, until, dry_run=True)
    assert dry.status_code == 200, dry.text
    assert _data_files(directory) == []
    assert not (directory / "_manifests").exists()

    wet = _backfill(hub, since, until)
    assert wet.status_code == 200, wet.text
    assert wet.json()["datasets"] == dry.json()["datasets"]
    assert wet.json()["datasets"][0] == {"dataset": "steps", "rows": 3, "files": 2}
    assert len(_data_files(directory)) == sum(c["files"] for c in wet.json()["datasets"])


def test_a_bad_window_or_dataset_is_422_naming_its_bound(tmp_path: Path) -> None:
    config = _real(tmp_path, datasets=("steps",), backfill_max_window=3600)
    hub, _ = _hub(tmp_path, config)
    now = hub.clock.now() - timedelta(hours=2)

    inverted = _backfill(hub, now, now)
    assert inverted.status_code == 422
    assert "until must be after since" in inverted.json()["detail"]
    wide = _backfill(hub, now, now + timedelta(seconds=3601))
    assert wide.status_code == 422
    assert "backfill_max_window (3600 seconds)" in wide.json()["detail"]
    wrong = _backfill(hub, now, now + timedelta(seconds=1), dataset="invocations")
    assert wrong.status_code == 422
    assert "invocations" in wrong.json()["detail"]
    future = _backfill(hub, hub.clock.now() - timedelta(seconds=1), hub.clock.now() + timedelta(seconds=1))
    assert future.status_code == 422
    assert "until must not be in the future" in future.json()["detail"]


def test_the_export_off_is_409_for_a_wet_run_and_a_dry_run_still_counts(tmp_path: Path) -> None:
    hub, _ = _hub(tmp_path)
    now = hub.clock.now()
    wet = _backfill(hub, now - timedelta(seconds=1), now)
    assert wet.status_code == 409
    assert "not configured" in wet.json()["detail"]
    dry = _backfill(hub, now - timedelta(seconds=1), now, dry_run=True)
    assert dry.status_code == 200, dry.text
    assert dry.json()["dry_run"] is True


def test_a_writer_failure_stops_the_backfill_with_502_and_the_counts_so_far(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path, _SETTLED)
    _sweep(hub, InMemoryEgressWriter()).sweep()
    since = hub.clock.now()
    _closed_step(hub, graph, 1)
    failing = InMemoryEgressWriter()
    failing.fail = EgressFailure(EgressFailureCause.LOW_DISK, "low", free_bytes=1, required_bytes=2)
    object.__setattr__(hub.services, "egress_backfill", _service(hub, _SETTLED, failing))
    until = hub.clock.now() + timedelta(seconds=1)
    hub.clock.advance(timedelta(seconds=1))

    resp = _backfill(hub, since, until)

    assert resp.status_code == 502
    body = resp.json()
    assert body["cause"] == "low-disk"
    assert body["datasets"] == [{"dataset": "steps", "rows": 0, "files": 0}]
    assert _kinds(hub) == []


def test_the_cli_reports_counts_and_renders_refusals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _real(tmp_path, backfill_max_window=3600)
    hub, graph = _hub(tmp_path, config)
    sweep = hub.services.egress_export
    assert sweep is not None
    sweep.sweep()
    _closed_step(hub, graph, 1)
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, *, json=None, params=None, timeout, **_: hub.client.post(url, json=json),
    )

    dry = CliRunner().invoke(
        hub_group,
        ["egress", "backfill", "--since", "2026-07-12T00:00:00", "--until", "2026-07-12T00:30:00", "--dry-run"],
        env={**_ENV, "TZ": "UTC"},
    )
    assert dry.exit_code == 0, dry.output
    assert "would write" in dry.output

    wide = CliRunner().invoke(
        hub_group,
        ["egress", "backfill", "--since", "2026-07-12T00:00:00", "--until", "2026-07-13T00:00:00"],
        env=_ENV,
    )
    assert wide.exit_code != 0
    assert "backfill_max_window" in wide.output


def _events_backfill(world: _Events, since: datetime, until: datetime, *, dry_run: bool = False) -> tuple:  # type: ignore[type-arg]
    written = InMemoryEgressWriter()
    config = EgressConfig(directory=Path("unused"), settle_seconds=0)
    result = _service(world.hub, config, written).backfill(since, until, dataset="events", dry_run=dry_run)
    return result, written


def test_an_events_backfill_by_step_start_after_an_upgrade_writes_every_current_derivation(tmp_path: Path) -> None:
    world = _Events(tmp_path)
    since = world.hub.clock.now() - timedelta(seconds=30)
    second = _closed_step(world.hub, world.graph, 2)
    until = world.hub.clock.now() + timedelta(seconds=1)
    world.segment("sg_a")
    world.segment("sg_b", second)
    world.derive("sg_a", 1, version=_OLD_VERSION)
    world.derive("sg_b", 2, version=_OLD_VERSION, chunk_id=second)
    world.hub.clock.advance(timedelta(days=3))
    world.derive("sg_a", 2)  # the upgrade re-derives sg_a long after the window
    world.hub.clock.advance(timedelta(days=1))
    world.derive("sg_b", 1, chunk_id=second)  # last derived after the window, before the export was enabled

    result, written = _events_backfill(world, since, until)

    rows = _rows(written, "events")
    derivations = [
        (r["segment_id"], r["extractor_version"], r["event_count"]) for r in rows if r["record_type"] == "derivation"
    ]
    assert sorted(derivations) == [("sg_a", EXTRACTOR_VERSION, 2), ("sg_b", EXTRACTOR_VERSION, 1)]
    assert [(c.dataset, c.rows) for c in result.datasets] == [("events", len(rows))]
    assert len(rows) == 5
    assert all(p.backfill and p.extractor_version == EXTRACTOR_VERSION for p, _ in written.manifests)


def test_an_events_backfill_skips_a_step_that_started_outside_the_window_whatever_its_derived_at(
    tmp_path: Path,
) -> None:
    world = _Events(tmp_path)  # this chunk's step starts before the window
    world.hub.clock.advance(timedelta(hours=1))
    since = world.hub.clock.now()
    inside = _closed_step(world.hub, world.graph, 2)
    world.segment("sg_old")
    world.segment("sg_new", inside)
    world.derive("sg_old", 1)  # derived inside the window
    world.derive("sg_new", 1, chunk_id=inside)
    until = world.hub.clock.now() + timedelta(seconds=1)
    world.hub.clock.advance(timedelta(seconds=1))

    _, written = _events_backfill(world, since, until)

    assert {r["segment_id"] for r in _rows(written, "events")} == {"sg_new"}


def test_an_events_dry_run_counts_without_writing(tmp_path: Path) -> None:
    world = _Events(tmp_path)
    since = world.hub.clock.now() - timedelta(seconds=30)
    world.segment("sg_a")
    world.derive("sg_a", 2)
    world.drop("sg_a")
    until = world.hub.clock.now()

    dry, nothing = _events_backfill(world, since, until, dry_run=True)
    wet, written = _events_backfill(world, since, until)

    assert nothing.batches == []
    assert dry.dry_run
    assert [(c.dataset, c.rows, c.files) for c in dry.datasets] == [(c.dataset, c.rows, c.files) for c in wet.datasets]
    assert [r["record_type"] for r in _rows(written, "events")] == ["dropped"]


def test_the_cli_refuses_a_future_until_before_sending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, _ = _hub(tmp_path, _real(tmp_path, backfill_max_window=3600))
    sent: list[object] = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: sent.append(a))
    now = hub.clock.now()
    monkeypatch.setattr("blizzard.cli.window.SystemClock", lambda: hub.clock)

    result = CliRunner().invoke(
        hub_group,
        [
            "egress",
            "backfill",
            "--since",
            "2026-07-12T00:00:00",
            "--until",
            (now + timedelta(seconds=1)).astimezone().replace(tzinfo=None).isoformat(),
        ],
        env=_ENV,
    )

    assert result.exit_code != 0
    assert "--until must not be in the future" in result.output
    assert sent == []
