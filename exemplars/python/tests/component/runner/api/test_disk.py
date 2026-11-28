"""The host's disk route over a served host whose root adds the disk area (component tier)."""

# exemplar: bzh:dependency-injection. Production mounts the router on a host lane in ``create_app``; here the stand-in
# root adds the area and the router mounts on a host lane, so each request resolves through ``host_request``.

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.runner import composition
from blizzard.runner.api.resolution import host_request
from blizzard.runner.app import HostedApp, Lane, build_hosted_app
from blizzard.runner.config import RunnerConfig
from exemplar.runner.api import disk
from exemplar.runner.disk.reading import DiskReading, HostDiskReader
from exemplar.runner.host_root import open_serving
from tests.support.host_files import host_config_of
from tests.support.runner_fakes import make_store

_NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def hosted(tmp_path: Path) -> Iterator[HostedApp]:
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    make_store(config.db_url)
    host_config = host_config_of(config)
    host = composition.build_host_process(host_config)
    host.clock = FixedClock(_NOW)
    served = build_hosted_app(config, open_serving(host, host_config))
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
    assert len(served) == 2
    assert all(reader is held for reader in served)
