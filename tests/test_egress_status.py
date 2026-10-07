"""``GET /api/egress/status`` and ``blizzard hub egress status`` (component tier) — composed from the settings and
the facts the sweep leaves behind."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from blizzard.hub.cli import hub as hub_group
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.hub.egress.factory import EgressUnavailable
from tests.support import HubHarness
from tests.test_egress_sweep import _closed_step, _hub

pytestmark = pytest.mark.component


def _on(tmp_path: Path, **extra: object) -> EgressConfig:
    (tmp_path / "out").mkdir(exist_ok=True)
    return EgressConfig(directory=tmp_path / "out", settle_seconds=0, min_free_bytes=0, **extra)  # type: ignore[arg-type]


def _status(hub: HubHarness) -> dict:  # type: ignore[type-arg]
    resp = hub.client.get("/api/egress/status")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_a_hub_with_the_export_off_reports_it_off(tmp_path: Path) -> None:
    hub, _ = _hub(tmp_path)
    body = _status(hub)
    assert body["state"] == "off"
    assert body["directory"] is None
    assert body["datasets"] == []
    assert body["free_bytes"] is None


def test_a_rejected_export_names_the_setting_and_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "blizzard.hub.composition.build_egress_writer", lambda *_: EgressUnavailable("parquet needs pyarrow")
    )
    config = EgressConfig(directory=tmp_path / "out", format="parquet")
    hub, _ = _hub(tmp_path, config)
    body = _status(hub)
    assert body["state"] == "rejected"
    assert body["rejected_setting"] == "egress.format"
    assert body["rejected_value"] == "parquet"
    assert body["datasets"] == []
    assert body["free_bytes"] is None


def test_the_cursors_their_lag_the_last_pass_the_last_file_and_the_free_space(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path, _on(tmp_path))
    sweep = hub.services.egress_export
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

    chunk_id = _closed_step(hub, graph, 1, usage=2)
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


def test_the_last_error_is_ongoing_until_a_write_recovers(tmp_path: Path) -> None:
    config = EgressConfig(directory=tmp_path / "never-made", settle_seconds=0, min_free_bytes=0)
    hub, graph = _hub(tmp_path, config)
    sweep = hub.services.egress_export
    assert sweep is not None
    sweep.sweep()
    assert _status(hub)["last_error_at"] is None
    _closed_step(hub, graph, 1)
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


def test_the_cli_renders_the_status_and_its_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, _ = _hub(tmp_path, _on(tmp_path))
    sweep = hub.services.egress_export
    assert sweep is not None
    sweep.sweep()
    monkeypatch.setattr(httpx, "get", lambda url, *, params=None, timeout, **_: hub.client.get(url, params=params))

    text = CliRunner().invoke(hub_group, ["egress", "status"], env={"BZ_HUB_URL": "http://hub.local:8421"})
    assert text.exit_code == 0, text.output
    assert "egress: on, writing ndjson to" in text.output
    assert "steps: cursor" in text.output
    assert "free space:" in text.output

    as_json = CliRunner().invoke(hub_group, ["egress", "status", "--json"], env={"BZ_HUB_URL": "http://hub.local:8421"})
    assert as_json.exit_code == 0, as_json.output
    assert '"state": "on"' in as_json.output
