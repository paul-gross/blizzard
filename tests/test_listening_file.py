"""``--listening-file`` and ``--port 0``: a daemon binds first, derives every URL from the bound socket, and
publishes its URL once serving (component tier).

Every daemon here binds port 0, so the kernel picks the port and no test chooses one."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import threading
import time
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import httpx2
import pytest
from fastapi import FastAPI

from blizzard.cli.runtime import bound_port, bound_tcp, build_early_shutdown_server, publish_listening_file
from blizzard.runner.composition import build_host_process
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.adapter import WorkerPreamble
from blizzard.runner.harness.harness_shared import build_identity_env
from blizzard.runner.host_config import BuiltRunner, ResolvedToken
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.listeners import AdoptedTcp, Uds
from blizzard.runner.loop_wiring import LoopWiring
from blizzard.runner.process.child_env import AllowlistedEnv
from tests.host_files import host_config_of
from tests.runner_fakes import FIXTURE_RUNNER_ID, FakeHub, migrated_store_at

pytestmark = pytest.mark.component

_HOST = "127.0.0.1"
_DEADLINE_SECONDS = 10.0


def _wait_for(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + _DEADLINE_SECONDS
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met before the deadline")
        time.sleep(0.01)


def _gated_app(release: threading.Event) -> FastAPI:
    """An app whose startup waits on ``release``, so a test can look at the world mid-startup."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        while not release.is_set():
            await asyncio.sleep(0.01)
        yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


@contextmanager
def _serving(app: FastAPI, listening_file: Path) -> Generator[int, None, None]:
    """APP served on a kernel-picked port in a thread; yields the bound port, stops the server on exit."""
    with bound_tcp(_HOST, 0) as sock:
        port = bound_port(sock)
        server = build_early_shutdown_server(
            app, host=_HOST, port=port, shutdown_signal=asyncio.Event(), listening_file=listening_file
        )
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            yield port
        finally:
            server.should_exit = True
            thread.join(_DEADLINE_SECONDS)


def test_the_listening_file_holds_the_bound_port_and_appears_only_once_serving(tmp_path: Path) -> None:
    listening_file = tmp_path / "daemon.url"
    release = threading.Event()
    with _serving(_gated_app(release), listening_file) as port:
        time.sleep(0.2)  # startup is under way and held at the lifespan
        assert not listening_file.exists()

        release.set()
        _wait_for(listening_file.exists)

        url = listening_file.read_text()
        assert url == f"http://{_HOST}:{port}\n"
        assert port != 0
        assert httpx2.get(f"{url.strip()}/api/health").json() == {"status": "ok"}


def test_a_failed_startup_publishes_no_listening_file(tmp_path: Path) -> None:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        raise RuntimeError("startup refused")
        yield

    listening_file = tmp_path / "daemon.url"
    with bound_tcp(_HOST, 0) as sock:
        server = build_early_shutdown_server(
            FastAPI(lifespan=lifespan),
            host=_HOST,
            port=bound_port(sock),
            shutdown_signal=asyncio.Event(),
            listening_file=listening_file,
        )
        with pytest.raises(SystemExit):
            asyncio.run(server.serve(sockets=[sock]))
    assert not listening_file.exists()


def test_a_reader_never_observes_a_partial_listening_file(tmp_path: Path) -> None:
    listening_file = tmp_path / "daemon.url"
    whole = {f"http://{_HOST}:{port}\n" for port in (1, 65535)}
    seen: set[str] = set()
    done = threading.Event()

    def read() -> None:
        while not done.is_set():
            with contextlib.suppress(FileNotFoundError):
                seen.add(listening_file.read_text())

    reader = threading.Thread(target=read)
    reader.start()
    try:
        for _ in range(500):
            for port in (1, 65535):
                publish_listening_file(listening_file, _HOST, port)
    finally:
        done.set()
        reader.join()

    assert seen <= whole
    assert sorted(path.name for path in tmp_path.iterdir()) == ["daemon.url"]


def test_a_host_composed_on_a_bound_port_zero_derives_every_runner_url_from_the_bound_port(tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path,
        db_url=HostLayout(tmp_path).default_db_url,
        hub_url="http://127.0.0.1:9",
        host=_HOST,
        port=0,
        runner_id=FIXTURE_RUNNER_ID,
    )
    config.data_dir.mkdir(parents=True, exist_ok=True)
    migrated_store_at(config.db_url)

    with bound_tcp(_HOST, 0) as sock:
        port = bound_port(sock)
        host_config = dataclasses.replace(host_config_of(config), port=port)
        (projection,) = host_config.projections({"BZ_HUB_TOKEN_RUNNER_LOCAL": "token"})
        assert isinstance(projection, BuiltRunner)
        built = BuiltRunner(projection.id or FIXTURE_RUNNER_ID, projection.config, ResolvedToken())
        host = build_host_process(host_config, [built], tcp_socket=sock)
        try:
            (runner,) = host.runners
            expected = f"http://{_HOST}:{port}/runners/{runner.runner_id}"
            assert runner.config.local_api_url == expected

            assert isinstance(host.listeners.tcp, AdoptedTcp)
            sockets = host.listeners.bound()
            try:
                assert sockets[1] is sock
            finally:
                sockets[0].close()
                Uds(runner.config.socket_path).unlink()

            ctx = LoopWiring(runner.config, "").context(FakeHub(), runner)
            assert ctx.config.local_api_url == expected
            preamble = WorkerPreamble(environments=[], lease_id="lease_1", local_api_url=ctx.config.local_api_url)
            env = build_identity_env(preamble, "ch_1", "sess", AllowlistedEnv.of(()))
            assert env["BLIZZARD_RUNNER_URL"] == expected
        finally:
            host.close()


def test_publish_creates_a_missing_directory_and_leaves_no_temp_file(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "hub.url"
    publish_listening_file(target, "127.0.0.1", 8123)
    assert target.read_text() == "http://127.0.0.1:8123\n"
    assert [entry.name for entry in target.parent.iterdir()] == ["hub.url"]


def test_bound_tcp_serves_an_ipv6_host() -> None:
    with bound_tcp("::1", 0) as sock:
        assert bound_port(sock) > 0


def test_publish_brackets_an_ipv6_host(tmp_path: Path) -> None:
    target = tmp_path / "hub.url"
    publish_listening_file(target, "::1", 8123)
    assert target.read_text() == "http://[::1]:8123\n"
