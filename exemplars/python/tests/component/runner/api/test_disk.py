"""The host's disk route over a served host whose root adds the disk area (component tier)."""

# exemplar: bzh:dependency-injection. Production mounts the router on a host lane in ``create_app``; here the stand-in
# root adds the area and the router mounts on a host lane, so each request resolves through ``host_request``.

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Never

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event

from blizzard.foundation.clock import FixedClock, SystemMonotonicClock
from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing
from blizzard.runner import composition, wiring
from blizzard.runner.api.resolution import host_request
from blizzard.runner.app import HostedApp, Lane, build_hosted_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.hub.host_options import HostHubOptions
from blizzard.runner.loop.host_tick import HostTick, HostTickInForce
from blizzard.runner.stores import HostStores
from blizzard.runner.tracing.host_options import HostTracingOptions
from exemplar.runner.api import disk
from exemplar.runner.disk.reading import DiskReading, HostDiskReader
from exemplar.runner.host_root import open_infrastructure, open_serving
from tests.support.host_files import host_config_of
from tests.support.runner_fakes import make_store

_NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def hosted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[HostedApp]:
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    make_store(config.db_url)
    host_config = host_config_of(config)

    def resolve_clock(environ: Mapping[str, str]) -> FixedClock:
        return FixedClock(_NOW)

    monkeypatch.setattr(composition, "resolve_clock", resolve_clock)
    served = build_hosted_app(config, open_serving(host_config))
    Lane((Depends(host_request),), (disk.router,)).mount(served.app)
    routes = served.app.router.routes
    routes.insert(0, routes.pop())  # ahead of the embedded frontend's catch-all
    try:
        yield served
    finally:
        served.close()


def test_the_disk_route_answers_the_host_roots_free_space_at_the_hosts_clock(hosted: HostedApp) -> None:
    with TestClient(hosted.app) as client:
        resp = client.get("/api/host/disk")

    assert resp.status_code == 200, resp.text
    assert resp.json()["free_bytes"] > 0
    assert resp.json()["read_at"] == "2026-10-15T12:00:00+00:00"


def test_every_request_resolves_the_one_reader_the_host_scope_holds(
    hosted: HostedApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    served: list[HostDiskReader] = []
    read = HostDiskReader.read

    def recording(reader: HostDiskReader) -> DiskReading:
        served.append(reader)
        return read(reader)

    monkeypatch.setattr(HostDiskReader, "read", recording)
    with TestClient(hosted.app) as client:
        assert client.get("/api/host/disk").status_code == 200
        assert client.get("/api/host/disk").status_code == 200

    held = hosted.root.owner.resolver.get(HostDiskReader)
    assert hosted.opened is not None
    assert hosted.root.owner.resolver.get(HostStores) is hosted.opened.host.host_stores
    assert len(served) == 2
    assert all(reader is held for reader in served)


def test_serving_root_closes_runners_before_its_infrastructure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    make_store(config.db_url)
    opened = open_serving(host_config_of(config))
    closed: list[str] = []
    close_all = opened.root.runners.close_all

    def close_runners() -> None:
        closed.append("runners")
        close_all()

    def dispose_engine(engine: Engine) -> None:
        closed.append("engine")

    monkeypatch.setattr(opened.root.runners, "close_all", close_runners)
    event.listen(opened.host.engine, "engine_disposed", dispose_engine)
    opened.close()
    assert closed == ["runners", "engine"]


def test_infrastructure_composes_disk_over_borrowed_clock_and_leaves_borrowed_tracing_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    make_store(config.db_url)
    tracing = DisabledPlatformTracing()
    shutdowns: list[float] = []

    def shutdown(timeout: float) -> None:
        shutdowns.append(timeout)

    monkeypatch.setattr(tracing, "shutdown", shutdown)

    def unused_report() -> Never:
        raise AssertionError("the unregistered host does not report")

    owner = open_infrastructure(
        config,
        FixedClock(_NOW),
        SystemMonotonicClock(),
        wiring.HostRuntimeOptions({}),
        HostHubOptions(config.hub_url, "", unused_report, lambda: ()),
        HostTracingOptions(config.tracing, {}, (), platform_tracing=tracing),
        tick_in_force=HostTickInForce(HostTick(), ()),
    )
    disposed: list[Engine] = []
    event.listen(owner.resolver.get(Engine), "engine_disposed", disposed.append)
    try:
        reader = owner.resolver.get(HostDiskReader)
        assert reader is owner.resolver.get(HostDiskReader)
        assert reader.read().at == _NOW
    finally:
        owner.close()
    assert len(disposed) == 1
    assert shutdowns == []
