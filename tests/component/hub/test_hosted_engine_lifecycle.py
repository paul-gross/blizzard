"""Hosted composition releases its SQLite pool before suite teardown."""

import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from blizzard.cli.main import blizzard
from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing
from blizzard.hub import app as hub_app
from blizzard.hub.cli import runtime as hub_cli
from blizzard.hub.composition import TenantCache
from blizzard.hub.config import HubConfig
from blizzard.hub.delivery.action_loop import ActionLoop
from blizzard.hub.domain.runners.version_window import VersionWindow
from tests.support.config_inputs import build_hosted_hub_app, init_hub
from tests.support.open_file_guard import inspect


def _pooled_connection(engine: Engine) -> sqlite3.Connection:
    with engine.connect() as connection:
        raw = connection.connection.driver_connection
    assert isinstance(raw, sqlite3.Connection)
    return raw


def _assert_closed(raw: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        raw.execute("SELECT 1")


def test_lifespan_releases_engine(tmp_path: Path) -> None:
    app = build_hosted_hub_app(init_hub(tmp_path / "hub"))
    with TestClient(app):
        raw = _pooled_connection(app.state.engine)
    _assert_closed(raw)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires /proc/self/fd")
@pytest.mark.parametrize("fail_startup", [False, True])
def test_lifespan_releases_sqlite_file_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_startup: bool
) -> None:
    app = build_hosted_hub_app(init_hub(tmp_path / "hub"))
    database = app.state.engine.url.database
    assert database is not None
    path = Path(database).resolve()
    paths = {path, Path(f"{path}-wal"), Path(f"{path}-shm")}
    _pooled_connection(app.state.engine)
    assert inspect(None, paths)

    if fail_startup:

        def fail(_app: object) -> None:
            raise RuntimeError("startup failed")

        monkeypatch.setattr(hub_app.Sweep, "all", fail)
        with pytest.raises(RuntimeError, match="startup failed"), TestClient(app):
            pass
    else:
        with TestClient(app):
            assert inspect(None, paths)
    assert inspect(None, paths) == []


def test_failed_startup_releases_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_hosted_hub_app(init_hub(tmp_path / "hub"))
    raw = _pooled_connection(app.state.engine)

    def fail(_app: object) -> None:
        raise RuntimeError("startup failed")

    monkeypatch.setattr(hub_app.Sweep, "all", fail)
    with pytest.raises(RuntimeError, match="startup failed"), TestClient(app):
        pass
    _assert_closed(raw)


def test_partial_startup_joins_started_sweep_before_disposal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_hosted_hub_app(init_hub(tmp_path / "hub"))
    raw = _pooled_connection(app.state.engine)
    finished: list[bool] = []

    class Sweep:
        async def run(self) -> None:
            await app.state.shutdown.wait()
            raw.execute("SELECT 1")
            finished.append(True)

    def sweeps(_app: object) -> Iterator[Sweep]:
        yield Sweep()
        raise RuntimeError("partial startup")

    monkeypatch.setattr(hub_app.Sweep, "all", sweeps)
    with pytest.raises(RuntimeError, match="partial startup"), TestClient(app):
        pass
    assert finished == [True]
    _assert_closed(raw)


def test_failed_shutdown_releases_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stopped: list[bool] = []
    original_stop = ActionLoop.stop

    def stop(loop: ActionLoop) -> None:
        stopped.append(True)
        original_stop(loop)

    def fail(_cache: TenantCache) -> None:
        raise RuntimeError("shutdown failed")

    monkeypatch.setattr(ActionLoop, "stop", stop)
    monkeypatch.setattr(TenantCache, "close_served", fail)
    app = build_hosted_hub_app(init_hub(tmp_path / "hub"))
    raw = _pooled_connection(app.state.engine)
    with pytest.raises(RuntimeError, match="shutdown failed"), TestClient(app):
        pass
    assert stopped
    _assert_closed(raw)


def test_failed_sweep_joins_other_sweeps_before_disposal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_hosted_hub_app(init_hub(tmp_path / "hub"))
    raw = _pooled_connection(app.state.engine)
    finished: list[bool] = []

    class WaitingSweep:
        async def run(self) -> None:
            await app.state.shutdown.wait()
            raw.execute("SELECT 1")
            finished.append(True)

    class FailingSweep:
        async def run(self) -> None:
            await app.state.shutdown.wait()
            raise RuntimeError("sweep failed")

    def sweeps(_app: object) -> list[WaitingSweep | FailingSweep]:
        return [WaitingSweep(), FailingSweep()]

    monkeypatch.setattr(hub_app.Sweep, "all", sweeps)
    with pytest.raises(RuntimeError, match="sweep failed"), TestClient(app):
        pass
    assert finished == [True]
    _assert_closed(raw)


def test_failed_construction_releases_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = init_hub(tmp_path / "hub")
    original = hub_app.open_hub_engine
    connections: list[sqlite3.Connection] = []
    paths: set[Path] = set()

    def open_engine(url: str) -> Engine:
        engine = original(url)
        connections.append(_pooled_connection(engine))
        assert engine.url.database is not None
        path = Path(engine.url.database).resolve()
        paths.update({path, Path(f"{path}-wal"), Path(f"{path}-shm")})
        if sys.platform.startswith("linux"):
            assert inspect(None, paths)
        return engine

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("construction failed")

    monkeypatch.setattr(hub_app, "open_hub_engine", open_engine)
    monkeypatch.setattr(hub_app, "build_hub_core", fail)
    with pytest.raises(RuntimeError, match="construction failed"):
        build_hosted_hub_app(config)
    _assert_closed(connections[0])
    assert inspect(None, paths) == []


@pytest.mark.parametrize("caller_owned", [False, True])
@pytest.mark.parametrize("fail_open", [False, True])
def test_failed_construction_shuts_down_only_owned_tracing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caller_owned: bool, fail_open: bool
) -> None:
    class Tracing(DisabledPlatformTracing):
        def __init__(self) -> None:
            super().__init__()
            self.shutdown_timeouts: list[float] = []

        def shutdown(self, timeout: float) -> None:
            self.shutdown_timeouts.append(timeout)

    config = init_hub(tmp_path / "hub")
    tracing = Tracing()

    def build_tracing(*args: object, **kwargs: object) -> Tracing:
        return tracing

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("construction failed")

    monkeypatch.setattr(hub_app, "build_platform_tracing", build_tracing)
    monkeypatch.setattr(hub_app, "open_hub_engine" if fail_open else "build_hub_core", fail)
    with pytest.raises(RuntimeError, match="construction failed"):
        build_hosted_hub_app(config, platform_tracing=tracing if caller_owned else None)
    assert tracing.shutdown_timeouts == ([] if caller_owned else [hub_app.PLATFORM_TRACING_SHUTDOWN_SECONDS])


def test_late_construction_failure_stops_services_before_disposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = init_hub(tmp_path / "hub")
    original_open = hub_app.open_hub_engine
    original_stop = ActionLoop.stop
    connections: list[sqlite3.Connection] = []
    stopped: list[bool] = []

    def open_engine(url: str) -> Engine:
        engine = original_open(url)
        connections.append(_pooled_connection(engine))
        return engine

    def stop(loop: ActionLoop) -> None:
        connections[0].execute("SELECT 1")
        original_stop(loop)
        stopped.append(True)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("late construction failure")

    monkeypatch.setattr(hub_app, "open_hub_engine", open_engine)
    monkeypatch.setattr(ActionLoop, "stop", stop)
    monkeypatch.setattr(hub_app, "create_app", fail)
    with pytest.raises(RuntimeError, match="late construction failure"):
        build_hosted_hub_app(config)
    assert stopped == [True]
    _assert_closed(connections[0])


def test_cli_failure_before_lifespan_closes_hosted_resources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "hub"
    init_hub(root)
    original_build = hub_cli.build_hosted_app
    connections: list[sqlite3.Connection] = []
    stopped: list[bool] = []
    original_stop = ActionLoop.stop

    def build(config: HubConfig, *, version_window: VersionWindow) -> FastAPI:
        app = original_build(config, version_window=version_window)
        connections.append(_pooled_connection(app.state.engine))
        app.state.services.action_loop.start()
        return app

    def stop(loop: ActionLoop) -> None:
        connections[0].execute("SELECT 1")
        original_stop(loop)
        stopped.append(True)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("server construction failed")

    monkeypatch.setattr(hub_cli, "build_hosted_app", build)
    monkeypatch.setattr(hub_cli, "build_early_shutdown_server", fail)
    monkeypatch.setattr(ActionLoop, "stop", stop)
    result = CliRunner().invoke(blizzard, ["hub", "host", "--dir", str(root), "--port", "0"])
    assert isinstance(result.exception, RuntimeError)
    assert str(result.exception) == "server construction failed"
    assert stopped == [True]
    _assert_closed(connections[0])


def test_borrowed_oauth_client_remains_open(tmp_path: Path) -> None:
    with httpx2.Client() as client:
        app = build_hosted_hub_app(init_hub(tmp_path / "hub"), oauth_http_client=client)
        with TestClient(app):
            pass
        assert not client.is_closed


@pytest.mark.parametrize("fail_construction", [False, True])
def test_owned_oauth_client_closes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_construction: bool) -> None:
    config = init_hub(tmp_path / "hub")
    original_client = httpx2.Client
    clients: list[httpx2.Client] = []

    def client(*, timeout: float) -> httpx2.Client:
        result = original_client(timeout=timeout)
        clients.append(result)
        return result

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("instrumentation failed")

    monkeypatch.setattr(hub_app.httpx2, "Client", client)
    if fail_construction:
        monkeypatch.setattr(hub_app, "build_hub_flags", fail)
        with pytest.raises(RuntimeError, match="instrumentation failed"):
            build_hosted_hub_app(config)
    else:
        app = build_hosted_hub_app(config)
        with TestClient(app):
            assert not clients[0].is_closed
    assert len(clients) == 1 and clients[0].is_closed
