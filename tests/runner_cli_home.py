"""Helpers for the runner status cli tests, shared with other test modules."""

from __future__ import annotations

import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
import uvicorn

from blizzard.foundation.runner_identity_state import IdentityState
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.app import build_hosted_app
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.cli.daemon import RunnerDaemon
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.listeners import Listeners, Uds
from tests.host_files import load_runner_config
from tests.runner_client import runner_seams
from tests.runner_fakes import SqlAlchemyRunnerStore, runner_store_errors
from tests.runner_init_fakes import init_runner

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _store(root: Path, runner_id: str | None = None) -> SqlAlchemyRunnerStore:
    """The host's store, bound to ``runner_id`` — by default the runner its host file configures."""
    config = load_runner_config(root)
    return SqlAlchemyRunnerStore(
        create_engine_from_url(config.db_url), runner_store_errors(), runner_id=runner_id or config.runner_id
    )


def registered_store(root: Path) -> SqlAlchemyRunnerStore:
    """The store with an identity row recorded — the local API reads only a registered runner's rows."""
    store = _store(root)
    store.record_runner_identity(
        RunnerIdentity(runner_id=store.connections.runner_id or "", runner_name="runner-local", registered_at=_NOW)
    )
    return store


def _edit_host_file(root: Path, old: str, new: str) -> None:
    """Replace ``old`` with ``new`` in the host directory's host file, which must hold it."""
    path = root / "blizzard-host.toml"
    text = path.read_text()
    assert old in text, f"{old!r} is not in the scaffolded host file:\n{text}"
    path.write_text(text.replace(old, new))


def init_runner_home(tmp_path: Path) -> Path:
    root = tmp_path / "runner"
    result = init_runner().invoke(runner_group, ["init", str(root)])
    assert result.exit_code == 0, result.output
    # These fixtures seed winter-style /ws/e* workdirs, rather than the new basic
    # provider's per-chunk folders. Pin the provider so takeover cwd stays /ws/e*.
    _edit_host_file(root, 'provider = "basic"', 'provider = "winter"')
    return root


@contextmanager
def serve_local_api(
    root: Path,
    *,
    trace_status: bool = True,
    refusing: tuple[str, str] | None = None,
    version_refused: str | None = None,
) -> Generator[tuple[Path, str]]:
    config = load_runner_config(root, port=0)
    app = build_hosted_app(config).app
    if refusing is not None:  # (store runner, token runner): the verdict the loop's check would hold
        runner_seams(app).identity_verdict.record(
            IdentityState.MISMATCHED,
            store_runner_id=refusing[0],
            token_runner_id=refusing[1],
            checked_at=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
        )
    if version_refused is not None:
        runner_seams(app).identity_verdict.refuse_version(version_refused)
    if not trace_status:
        runner_seams(app).trace_status = None  # `GET /api/traces/status` then refuses with a 503
    sockets = Listeners.of(config).bound()
    tcp_url = f"http://{sockets[1].getsockname()[0]}:{sockets[1].getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run(sockets=sockets), daemon=True)
    thread.start()
    try:
        _await_socket(config.socket_path)
        yield config.socket_path, tcp_url
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)
        Uds(config.socket_path).unlink()


def _await_socket(path: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    transport = httpx2.HTTPTransport(uds=str(path))
    with httpx2.Client(transport=transport, base_url="http://runner") as client:
        while time.monotonic() < deadline:
            try:
                if client.get("/api/health").status_code == 200:
                    return
            except httpx2.HTTPError:
                pass
            time.sleep(0.05)
    raise AssertionError(f"runner local API never came up on {path}")


def host_of(items: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch, paths: list[str]) -> None:
    """Every verb reaches a mock host whose runner list is ``items`` and which answers any other call as runner ``beta``."""

    def answer(request: httpx2.Request) -> httpx2.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/host/runners":
            return httpx2.Response(200, json={"items": items})
        return httpx2.Response(200, json={"runner_name": "beta"})

    def reach(
        cls: type[RunnerDaemon], verb: str, _dir: str, _url: str | None, _trace: object, runner: str | None = None
    ) -> RunnerDaemon:
        client = httpx2.Client(transport=httpx2.MockTransport(answer), base_url="http://host")
        return RunnerDaemon(verb, client, "host", runner)

    monkeypatch.setattr(RunnerDaemon, "reach", classmethod(reach))


TWO_RUNNERS: list[dict[str, object]] = [
    {"runner_id": "rn_a", "name": "alpha", "state": "serving", "halted_reasons": []},
    {"runner_id": "rn_b", "name": "beta", "state": "serving", "halted_reasons": []},
]
