"""``GET /api/egress/status`` and ``blizzard hub egress status`` (component tier) — composed from the settings and
the facts the sweep leaves behind."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx2
import pytest

from blizzard.hub.cli import hub as hub_group
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.hub.domain.observability.egress.repository import EgressCheckpoint, UsagePosition
from blizzard.hub.egress.factory import EgressUnavailable
from blizzard.hub.egress.writer import EgressWriterSettings
from blizzard.hub.store.internal.egress_store import EgressStore
from tests.support.egress_world import closed_step, egress_hub
from tests.support.hub_event_rows import hub_events_of
from tests.support.scaffolding import HubHarness, hub_level_connections
from tests.support.worker_http import bind_stubs


def _on(tmp_path: Path) -> EgressConfig:
    (tmp_path / "out").mkdir(exist_ok=True)
    return EgressConfig(directory=tmp_path / "out", settle_seconds=0, min_free_bytes=0)


def _status(hub: HubHarness) -> dict[str, Any]:
    resp = hub.client.get("/api/egress/status")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_a_hub_with_the_export_off_reports_it_off(tmp_path: Path) -> None:
    hub, _ = egress_hub(tmp_path)
    body = _status(hub)
    assert body["state"] == "off"
    assert body["directory"] is None
    assert body["datasets"] == []
    assert body["free_bytes"] is None


def test_a_rejected_export_names_the_setting_and_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def without_pyarrow(
        format: Literal["ndjson", "parquet"], directory: Path, settings: EgressWriterSettings, token: str
    ) -> EgressUnavailable:
        return EgressUnavailable("parquet needs pyarrow")

    monkeypatch.setattr("blizzard.hub.composition.build_egress_writer", without_pyarrow)
    config = EgressConfig(directory=tmp_path / "out", format="parquet")
    hub, _ = egress_hub(tmp_path, config)
    body = _status(hub)
    assert body["state"] == "rejected"
    assert body["rejected_setting"] == "egress.format"
    assert body["rejected_value"] == "parquet"
    assert body["datasets"] == []
    assert body["free_bytes"] is None


def test_the_cursors_their_lag_the_last_pass_the_last_file_and_the_free_space(tmp_path: Path) -> None:
    hub, graph = egress_hub(tmp_path, _on(tmp_path))
    sweep = hub.hub_services.egress_export
    assert sweep is not None
    sweep.sweep()
    start = _status(hub)
    assert start["state"] == "on"
    assert start["format"] == "ndjson"
    assert start["directory"] == str(tmp_path / "out")
    assert [(d["name"], d["lag_seconds"]) for d in start["datasets"]] == [("steps", None), ("invocations", None)]
    assert all(d["cursor_at"] is not None for d in start["datasets"])
    assert start["last_file"] is None
    assert start["free_bytes"] is not None
    assert start["min_free_bytes"] == 0

    chunk_id = closed_step(hub, graph, 1, usage=2)
    hub.clock.advance(timedelta(seconds=100))
    waiting = _status(hub)
    assert [d["lag_seconds"] for d in waiting["datasets"]] == [100, 105]

    sweep.sweep()
    told = _status(hub)
    assert [d["lag_seconds"] for d in told["datasets"]] == [None, None]
    assert told["last_pass_dataset"] in {"steps", "invocations"}  # one fixed-clock pass stamps both alike
    assert told["last_pass_row_count"] in {1, 2}
    assert told["last_pass_at"] is not None
    assert told["last_file"] is not None
    assert told["last_file"].startswith(("steps/", "invocations/"))
    assert chunk_id


def test_status_uses_append_order_for_position_and_recorded_time_for_pass_and_file(tmp_path: Path) -> None:
    hub, _ = egress_hub(tmp_path, _on(tmp_path))
    connections = hub_level_connections(hub.engine)
    store = EgressStore(
        connections, hub_events=hub_events_of(hub.engine), usage=hub.hub_services.hub_scoped_reads.corpus
    )
    now = hub.clock.now()
    written = EgressCheckpoint(
        "invocations",
        None,
        UsagePosition(now - timedelta(seconds=10)),
        7,
        ("invocations/data.ndjson", "invocations/manifest.json"),
        now,
    )
    assert store.append_cursor(written, after=None)
    reset = EgressCheckpoint(
        "invocations",
        None,
        UsagePosition(now - timedelta(seconds=30)),
        3,
        ("invocations/older-data.ndjson", "invocations/older-manifest.json"),
        now - timedelta(seconds=20),
    )
    store.reset_cursor(reset)

    status = _status(hub)
    invocations = next(dataset for dataset in status["datasets"] if dataset["name"] == "invocations")
    assert datetime.fromisoformat(invocations["cursor_at"]) == reset.usage.recorded_at
    assert datetime.fromisoformat(status["last_pass_at"]) == written.recorded_at
    assert status["last_pass_dataset"] == "invocations"
    assert status["last_pass_row_count"] == 7
    assert status["last_file"] == "invocations/data.ndjson"


def test_the_last_error_is_ongoing_until_a_write_recovers(tmp_path: Path) -> None:
    config = EgressConfig(directory=tmp_path / "never-made", settle_seconds=0, min_free_bytes=0)
    hub, graph = egress_hub(tmp_path, config)
    sweep = hub.hub_services.egress_export
    assert sweep is not None
    sweep.sweep()
    assert _status(hub)["last_error_at"] is None
    closed_step(hub, graph, 1)
    sweep.sweep()
    failed = _status(hub)
    assert failed["last_error_ongoing"] is True
    assert failed["last_error_at"] is not None
    assert failed["last_error_message"]

    (tmp_path / "never-made").mkdir()
    hub.clock.advance(timedelta(hours=1))
    sweep.sweep()
    recovered = _status(hub)
    assert recovered["last_error_ongoing"] is False
    assert recovered["last_error_at"] == failed["last_error_at"]


def test_the_cli_renders_the_status_and_its_json(tmp_path: Path) -> None:
    hub, _ = egress_hub(tmp_path, _on(tmp_path))
    sweep = hub.hub_services.egress_export
    assert sweep is not None
    sweep.sweep()

    def get(url: str, *, params: dict[str, str] | None = None, timeout: float, **_: object) -> httpx2.Response:
        return hub.client.get(url, params=params)

    bound = bind_stubs(get=get)

    text = bound.runner.invoke(hub_group, ["egress", "status"], env={"BZ_HUB_URL": "http://hub.local:8421"})
    assert text.exit_code == 0, text.output
    assert "egress: on, writing ndjson to" in text.output
    assert "steps: cursor" in text.output
    assert "free space:" in text.output

    as_json = bound.runner.invoke(
        hub_group, ["egress", "status", "--json"], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )
    assert as_json.exit_code == 0, as_json.output
    assert '"state": "on"' in as_json.output
