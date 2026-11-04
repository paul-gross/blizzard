"""The runner host on the controlled clock (component tier): the fence decides the clock once at the root, every runner
shares it, and the fenced host serves the clock routes the unfenced host answers 404 to."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.foundation.clock import ControlledClock, SystemClock
from blizzard.runner.app import build_hosted_app
from blizzard.runner.composition import HostProcess, build_host_process, build_runner_process
from blizzard.runner.config import RunnerConfig
from blizzard.runner.host_config import BuiltRunner, ResolvedToken
from blizzard.runner.host_layout import HostLayout
from tests.host_files import host_config_of
from tests.runner_fakes import migrated_store_at

pytestmark = pytest.mark.component

_RUNNERS = ("rn_a", "rn_b")
_FENCED = {"BLIZZARD_CLOCK": "controlled", "BLIZZARD_CLOCK_FENCE": "1"}


def _config(tmp_path: Path) -> RunnerConfig:
    config = RunnerConfig(root=tmp_path, db_url=HostLayout(tmp_path).default_db_url, hub_url="http://127.0.0.1:9")
    config.data_dir.mkdir(parents=True, exist_ok=True)
    migrated_store_at(config.db_url)
    return config


def _host(tmp_path: Path, environ: Mapping[str, str]) -> HostProcess:
    config = _config(tmp_path)
    return build_host_process(
        host_config_of(config),
        [BuiltRunner(rid, dataclasses.replace(config, runner_id=rid), ResolvedToken()) for rid in _RUNNERS],
        environ=environ,
    )


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"BLIZZARD_CLOCK": "controlled"},
        {"BLIZZARD_CLOCK_FENCE": "1"},
        {"BLIZZARD_CLOCK": "controlled", "BLIZZARD_CLOCK_FENCE": "true"},
    ],
)
def test_an_unfenced_host_runs_on_the_system_clock_and_has_no_clock_route(
    tmp_path: Path, environ: Mapping[str, str]
) -> None:
    host = _host(tmp_path, environ)
    hosted = build_hosted_app(host.runners[0].config, host)
    try:
        assert isinstance(host.clock, SystemClock)
        with TestClient(hosted.app) as client:
            assert client.get("/api/host/clock").status_code == 404
            # A POST to any unmounted path meets the served shell's static mount, so the unfenced answer is
            # whatever an unknown route answers.
            unknown = client.post("/api/host/no-such-route", json={})
            assert client.post("/api/host/clock", json={"advance_seconds": 1}).status_code == unknown.status_code
    finally:
        host.close()


def test_a_fenced_host_shares_one_controlled_clock_across_its_runners_and_its_routes(tmp_path: Path) -> None:
    host = _host(tmp_path, _FENCED)
    hosted = build_hosted_app(host.runners[0].config, host)
    try:
        assert isinstance(host.clock, ControlledClock)
        assert all(runner.clock is host.clock for runner in host.runners)
        with TestClient(hosted.app) as client:
            before = client.get("/api/host/clock").json()["now"]
            moved = client.post("/api/host/clock", json={"advance_seconds": 3600})
            assert moved.status_code == 200
            assert moved.json()["previous"] == before
            hour = datetime.fromisoformat(moved.json()["now"]) - datetime.fromisoformat(before)
            assert hour == timedelta(hours=1)
            assert client.get("/api/host/clock").json()["now"] == moved.json()["now"]

            host_tick = client.post("/api/host/ticks").json()["started_at"]
            assert host_tick == moved.json()["now"]
            for rid in _RUNNERS:
                assert client.post(f"/runners/{rid}/api/ticks").json()["started_at"] == moved.json()["now"]
    finally:
        host.close()


def test_the_clock_routes_set_forward_and_refuse_the_rest(tmp_path: Path) -> None:
    host = _host(tmp_path, _FENCED)
    hosted = build_hosted_app(host.runners[0].config, host)
    try:
        with TestClient(hosted.app) as client:
            now = datetime.fromisoformat(client.get("/api/host/clock").json()["now"])
            later = (now + timedelta(days=1)).astimezone(UTC).isoformat()
            ok = client.post("/api/host/clock", json={"to": later})
            assert ok.status_code == 200
            assert datetime.fromisoformat(ok.json()["now"]) == datetime.fromisoformat(later)

            back = client.post("/api/host/clock", json={"to": now.isoformat()})
            assert back.status_code == 409
            assert back.json() == {"code": "clock_backwards"}

            assert client.post("/api/host/clock", json={}).status_code == 422
            both = {"to": later, "advance_seconds": 1}
            assert client.post("/api/host/clock", json=both).status_code == 422
            assert client.post("/api/host/clock", json={"to": "2030-01-01T00:00:00"}).status_code == 422
            assert client.post("/api/host/clock", json={"advance_seconds": -1}).status_code == 422
            assert client.post("/api/host/clock", json={"advance_seconds": 1e300}).status_code == 422
    finally:
        host.close()


def test_a_runner_process_ignores_the_fence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _FENCED.items():
        monkeypatch.setenv(key, value)
    runner = build_runner_process(_config(tmp_path), runner_id="rn_a")
    try:
        assert isinstance(runner.clock, SystemClock)
    finally:
        runner.close()
