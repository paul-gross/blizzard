"""The runner's own ``pause``/``start`` verbs, driven against a live daemon on a real socket."""

from __future__ import annotations

import dataclasses
import json
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import httpx2
import uvicorn

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.app import build_hosted_app
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.listeners import Listeners, Uds
from tests.host_files import load_runner_config
from tests.runner_fakes import SqlAlchemyRunnerStore, runner_store_errors
from tests.support_ingest_and_pause_verbs import (
    _init_runner,  # pyright: ignore[reportPrivateUsage]
)
from tests.worker_http import Bound, bind_local_only


def _store(root: Path) -> SqlAlchemyRunnerStore:
    """Read the runner's own store back to assert the fact the verb landed."""
    config = load_runner_config(root)
    return SqlAlchemyRunnerStore(
        create_engine_from_url(config.db_url), runner_store_errors(), runner_id=config.runner_id
    )


@contextmanager
def _serve_local_api(root: Path) -> Generator[tuple[Path, str]]:
    """A live runner daemon's local API on its real socket — yields (socket path, TCP url).

    Driven against a real server, since the socket itself is what has to work; TCP binds
    on an ephemeral port so a test never collides with a daemon on the box.
    """
    config = load_runner_config(root, port=0)
    # Pin the SSO auth-mode probe to a none-mode (unreachable) hub so the
    # human-lane gating resolves to the authless path these hub-free tests assume.
    config = dataclasses.replace(config, hub_url="http://127.0.0.1:1")
    app = build_hosted_app(config).app
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


def _no_hub(*allow_base_urls: str) -> Bound:
    """Fail loudly if a local verb reaches for the hub — it must never."""
    return bind_local_only(allow_base_urls=allow_base_urls)


def test_pause_patches_the_runners_own_local_api(tmp_path: Path) -> None:
    """`runner pause` sets this runner's own brake through its local API, over the socket."""
    root = _init_runner(tmp_path)
    bound = _no_hub()
    with _serve_local_api(root):
        result = bound.runner.invoke(runner_group, ["pause", "--dir", str(root), "--by", "alice"])

    assert result.exit_code == 0, result.output
    assert "locally paused" in result.output
    store = _store(root)
    assert store.local_paused() is True
    assert store.hub_paused() is False  # the hub's brake is a separate concept


def test_pause_succeeds_with_the_hub_unreachable(tmp_path: Path) -> None:
    """The whole point: the local brake does not depend on the hub. `_no_hub` makes any
    hub call an error, so this passing is the assertion itself."""
    root = _init_runner(tmp_path)
    bound = _no_hub()
    with _serve_local_api(root):
        result = bound.runner.invoke(runner_group, ["pause", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert _store(root).local_paused() is True


def test_start_clears_the_local_brake(tmp_path: Path) -> None:
    """`runner start` is the counterpart; facts append and the flag derives from the newest."""
    root = _init_runner(tmp_path)
    bound = _no_hub()
    with _serve_local_api(root):
        assert bound.runner.invoke(runner_group, ["pause", "--dir", str(root)]).exit_code == 0
        result = bound.runner.invoke(runner_group, ["start", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert "no longer locally paused" in result.output
    assert _store(root).local_paused() is False


def test_pause_reports_itself_upward_atomically(tmp_path: Path) -> None:
    """The brake and the fact reporting it upward are one write; asserting
    the buffered fact, not just the flag, is the point."""
    root = _init_runner(tmp_path)
    bound = _no_hub()
    with _serve_local_api(root):
        assert bound.runner.invoke(runner_group, ["pause", "--dir", str(root), "--by", "alice"]).exit_code == 0

    store = _store(root)
    assert store.local_paused() is True
    pending = store.pending_outbound()
    assert [f.kind for f in pending] == ["runner.locally_paused"]
    # Runner-scoped: it is about the runner, so it correlates to no chunk or lease.
    assert (pending[0].chunk_id, pending[0].lease_id) == (None, None)
    assert json.loads(pending[0].payload)["by"] == "alice"


def test_start_reports_the_resume_upward(tmp_path: Path) -> None:
    """Clearing the brake is reported too, FIFO behind the pause fact."""
    root = _init_runner(tmp_path)
    bound = _no_hub()
    with _serve_local_api(root):
        assert bound.runner.invoke(runner_group, ["pause", "--dir", str(root)]).exit_code == 0
        assert bound.runner.invoke(runner_group, ["start", "--dir", str(root)]).exit_code == 0

    kinds = [f.kind for f in _store(root).pending_outbound()]
    assert kinds == ["runner.locally_paused", "runner.locally_resumed"]


def test_pause_over_tcp_when_runner_url_is_given(tmp_path: Path) -> None:
    """--runner-url addresses the same API through its TCP door — same route, same effect."""
    root = _init_runner(tmp_path)
    with _serve_local_api(root) as (_sock, tcp_url):
        bound = _no_hub(tcp_url)
        result = bound.runner.invoke(runner_group, ["pause", "--runner-url", tcp_url])

    assert result.exit_code == 0, result.output
    assert _store(root).local_paused() is True
