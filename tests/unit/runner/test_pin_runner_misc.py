"""Pinning tests for runner decisions that were defended only by comment prose.

Each test is the executable form of a decision whose only defence was a paragraph of
argument, replacing that prose."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.auth_core import Role
from blizzard.runner.api.hub_proxy import HUB_RETRY_CEILING
from blizzard.runner.auth.session import RunnerSession, SessionCookie
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.cli.worker_call import READ_TIMEOUT
from blizzard.runner.config import RunnerConfig
from tests.config_inputs import create_runner_app
from tests.worker_http import bind_stubs


def test_an_unconfigured_session_minted_before_a_restart_is_refused_after_it() -> None:
    """With no secret configured the session is stateless: nothing but the HMAC over the
    daemon's per-process secret admits it, so a restart's fresh secret invalidates every live
    cookie rather than resolving it from a store row that outlived the process."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    session = RunnerSession(username="alice", role=Role.ADMIN, issued_at=now, expires_at=now + timedelta(hours=8))
    before_restart = b"secret-of-the-first-process"
    after_restart = b"secret-of-the-second-process"
    cookie = SessionCookie(before_restart).mint(session)

    assert SessionCookie(before_restart).read(cookie, now=now) == session
    assert SessionCookie(after_restart).read(cookie, now=now) is None


class _CountingHubServer(ThreadingHTTPServer):
    """The hub double's server, recording every path its handler served."""

    seen: list[str]


class _CountingHubHandler(BaseHTTPRequestHandler):
    """An oauth-mode hub double on a real port: a runner that probes it reads an IdP
    surface and gates its human lane."""

    def do_GET(self) -> None:  # BaseHTTPRequestHandler's own casing, not ours
        assert isinstance(self.server, _CountingHubServer)
        self.server.seen.append(self.path)
        body = json.dumps({"keys": []}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def test_the_default_hub_client_never_reaches_the_configured_hub_url(tmp_path: Path) -> None:
    """``create_app``'s own default must be a transport-level double answering 404, not
    a real client against ``config.hub_url``, so a coincidental live listener there
    cannot flip the human lane's gating outside the composition root's control — and the
    same holds for ``hub_proxy_client``, the ``HubProxy``-forward seam's own default."""
    server = _CountingHubServer(("127.0.0.1", 0), _CountingHubHandler)
    server.seen = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        config = RunnerConfig(
            root=tmp_path,
            db_url="sqlite://",
            name="runner-hermetic",
            hub_url=f"http://127.0.0.1:{server.server_address[1]}",
            public_urls=("http://runner-hermetic.example",),
        )
        # No hub_http_client, no hub_proxy_client: both defaults are under test.
        client = TestClient(create_runner_app(config))

        status = client.get("/api/runner").status_code
        proxy_status = client.get("/api/chunks/ch_hermetic").status_code
    finally:
        server.shutdown()
        server.server_close()

    assert status != 401
    assert proxy_status == 404  # the hermetic double's own answer, never the real hub's
    assert server.seen == []


def test_the_hub_retry_ceiling_leaves_the_worker_read_timeout_room() -> None:
    """The observed hosted-hub restart the retry ceiling exists to ride out ran a ``502``
    from ``16:33:37Z`` through ``16:33:47Z`` (10s), with the hub answering again by
    ``16:33:55Z`` — an 18s window from first failure to recovery. ``HUB_RETRY_CEILING``
    must span at least that window, and it must sit strictly inside ``READ_TIMEOUT``."""
    observed_restart_window_seconds = 18.0
    assert observed_restart_window_seconds <= HUB_RETRY_CEILING
    assert HUB_RETRY_CEILING < READ_TIMEOUT


class _FakeLocalResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


def test_the_deprecated_pm_items_cli_alias_still_reads_the_work_item(monkeypatch: pytest.MonkeyPatch) -> None:
    """A node's prompt is inlined into the store at mint and immutable, so graphs already
    minted name this verb forever — dropping the alias fails those workers mid-node with
    "no such command"."""
    calls: list[str] = []

    def fake_get(url: str, *, timeout: float, **_: object) -> _FakeLocalResponse:
        calls.append(url)
        return _FakeLocalResponse('{"items": []}')

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(
        runner_group,
        ["pm-items", "ch_1"],
        env={"BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/"},
    )

    assert result.exit_code == 0, result.output
    assert calls == ["http://127.0.0.1:8431/api/chunks/ch_1/work-items"]
    assert '"items": []' in result.output
