"""Pinning tests for runner decisions that were defended only by comment prose (issue #270).

Each test here is the executable form of a decision whose only defence was a paragraph
of argument: the reset-clean's ignored-file sweep, the explicit-worktree origin read,
the runner session's per-process signing secret, ``create_app``'s hermetic default hub
client, and the deprecated ``pm-items`` CLI alias.
"""

from __future__ import annotations

import json
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from blizzard.auth_core import Role
from blizzard.runner.app import create_app
from blizzard.runner.auth.session import RunnerSession, mint_session_cookie, verify_session_cookie
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.config import RunnerConfig
from blizzard.runner.environments.internal.git import SubprocessEnvGit

# --- runner/auth/session.py: the stateless, per-process-secret session ---------------


@pytest.mark.unit
def test_a_session_minted_before_a_restart_is_refused_after_it() -> None:
    """The session is stateless: nothing but the HMAC over the daemon's per-process
    secret admits it, so a restart's fresh secret invalidates every live cookie rather
    than resolving it from a store row that outlived the process."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    session = RunnerSession(username="alice", role=Role.ADMIN, issued_at=now, expires_at=now + timedelta(hours=8))
    before_restart = b"secret-of-the-first-process"
    after_restart = b"secret-of-the-second-process"
    cookie = mint_session_cookie(session, secret=before_restart)

    assert verify_session_cookie(cookie, secret=before_restart, now=now) == session
    assert verify_session_cookie(cookie, secret=after_restart, now=now) is None


# --- runner/environments/internal/git.py: the reset-on-acquire clean and origin read --


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _repo(root: Path, name: str, *, origin: str) -> Path:
    workdir = root / name
    workdir.mkdir(parents=True)
    _git(workdir, "init", "-b", "main")
    _git(workdir, "config", "user.email", "worker@example.test")
    _git(workdir, "config", "user.name", "Worker")
    _git(workdir, "remote", "add", "origin", origin)
    return workdir


@pytest.mark.component
def test_the_clean_sweeps_ignored_build_artifacts_out_with_the_outgoing_tenant(tmp_path: Path) -> None:
    """``-fdx``, not ``-fd``: a previous tenant's ignored files (build output, installed
    deps) leave with it, since the reprovision step that follows restores them. A tree
    that kept them is not reset to base."""
    env_workdir = tmp_path / "e1"
    repo = _repo(env_workdir, "toy", origin="file:///origins/toy.git")
    (repo / ".gitignore").write_text("build/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "baseline")
    (repo / "build").mkdir()
    (repo / "build" / "artifact.o").write_text("previous tenant's build output")
    (repo / "junk.txt").write_text("previous tenant's untracked scratch file")

    SubprocessEnvGit().clean_environment(env_workdir)

    assert not (repo / "junk.txt").exists()
    assert not (repo / "build").exists()
    assert (repo / ".gitignore").exists()  # tracked state is winter's to reset, untouched here


@pytest.mark.component
def test_origin_url_reads_the_named_worktree_not_the_process_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path is passed, never inferred from cwd: git walks *up* from cwd to find an
    enclosing repository, so a caller standing anywhere else would otherwise get a
    plausible-looking URL for some other repo instead of an error."""
    target = _repo(tmp_path, "toy-a", origin="file:///origins/toy-a.git")
    elsewhere = _repo(tmp_path, "toy-b", origin="file:///origins/toy-b.git")
    monkeypatch.chdir(elsewhere)

    assert SubprocessEnvGit().origin_url(target) == "file:///origins/toy-a.git"


# --- runner/app.py: the hermetic default hub client ----------------------------------


class _CountingHubHandler(BaseHTTPRequestHandler):
    """An oauth-mode hub double on a real port: a runner that probes it reads an IdP
    surface and gates its human lane."""

    def do_GET(self) -> None:  # BaseHTTPRequestHandler's own casing, not ours
        self.server.seen.append(self.path)  # type: ignore[attr-defined]
        body = json.dumps({"keys": []}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.mark.unit
def test_the_default_hub_client_never_reaches_the_configured_hub_url(tmp_path: Path) -> None:
    """``create_app``'s own default must be a transport-level double answering 404, not a
    real client against ``config.hub_url`` — otherwise a coincidental live listener at
    that address (this very daemon, dogfooded) flips the human lane's gating on outside
    the ``host`` composition root's control."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CountingHubHandler)
    server.seen: list[str] = []  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        config = RunnerConfig(
            root=tmp_path,
            db_url="sqlite://",
            runner_id="runner-hermetic",
            hub_url=f"http://127.0.0.1:{server.server_address[1]}",
            public_url="http://runner-hermetic.example",
        )
        client = TestClient(create_app(config))  # no hub_http_client: the default is under test

        status = client.get("/api/runner").status_code
    finally:
        server.shutdown()
        server.server_close()

    assert status != 401
    assert server.seen == []  # type: ignore[attr-defined]


# --- runner/cli.py: the deprecated `pm-items` alias ----------------------------------


class _FakeLocalResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


@pytest.mark.unit
def test_the_deprecated_pm_items_cli_alias_still_reads_the_work_item(monkeypatch: pytest.MonkeyPatch) -> None:
    """A node's prompt is inlined into the store at mint and immutable, so graphs already
    minted name this verb forever — dropping the alias fails those workers mid-node with
    "no such command"."""
    calls: list[str] = []

    def fake_get(url: str, *, timeout: float) -> _FakeLocalResponse:
        calls.append(url)
        return _FakeLocalResponse('{"items": []}')

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(
        runner_group,
        ["pm-items", "ch_1"],
        env={"BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/"},
    )

    assert result.exit_code == 0, result.output
    assert calls == ["http://127.0.0.1:8431/api/chunks/ch_1/work-items"]
    assert '"items": []' in result.output
