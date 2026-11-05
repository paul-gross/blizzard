"""Pinning tests for runner decisions that were defended only by comment prose.

Each test is the executable form of a decision whose only defence was a paragraph of
argument, replacing that prose."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.auth_core import Role
from blizzard.runner.auth.session import RunnerSession, SessionCookie
from blizzard.runner.config import RunnerConfig
from tests.support.child_launch import env_git
from tests.support.config_inputs import create_runner_app


def test_a_configured_session_secret_survives_a_restart_and_the_fallback_does_not(tmp_path: Path) -> None:
    """Two app instances sharing a configured secret accept each other's cookie; two with none
    each draw a fresh secret and refuse it."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    session = RunnerSession(username="alice", role=Role.ADMIN, issued_at=now, expires_at=now + timedelta(hours=8))

    def secret_of(config: RunnerConfig) -> bytes:
        app = create_runner_app(config)
        with TestClient(app):
            return app.state.session_secret

    configured = RunnerConfig(root=tmp_path, db_url="sqlite://", session_secret=b"k" * 48)
    cookie = SessionCookie(secret_of(configured)).mint(session)
    assert SessionCookie(secret_of(configured)).read(cookie, now=now) == session

    unset = RunnerConfig(root=tmp_path, db_url="sqlite://")
    cookie = SessionCookie(secret_of(unset)).mint(session)
    assert SessionCookie(secret_of(unset)).read(cookie, now=now) is None


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


def test_the_clean_sweeps_untracked_scratch_but_leaves_the_ignored_dependency_trees(tmp_path: Path) -> None:
    """``-fd``, not ``-fdx``: the outgoing tenant's untracked scratch goes, ignored files
    stay. Rebuilding the dependency trees they hold exceeds the acquire tick's budget, so
    resetting ignored state is a project's own provisioning concern."""
    env_workdir = tmp_path / "e1"
    repo = _repo(env_workdir, "toy", origin="file:///origins/toy.git")
    (repo / ".gitignore").write_text("build/\nnode_modules/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "baseline")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "dep.js").write_text("an installed dependency tree")
    (repo / "build").mkdir()
    (repo / "build" / "artifact.o").write_text("previous tenant's build output")
    (repo / "junk.txt").write_text("previous tenant's untracked scratch file")

    env_git().clean_environment(env_workdir)

    assert not (repo / "junk.txt").exists()
    assert (repo / "node_modules" / "dep.js").exists()
    assert (repo / "build").exists()
    assert (repo / ".gitignore").exists()  # tracked state is winter's to reset, untouched here


def test_origin_url_reads_the_named_worktree_not_the_process_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path is passed, never inferred from cwd: git walks *up* from cwd to find an
    enclosing repository, so a caller standing anywhere else would otherwise get a
    plausible-looking URL for some other repo instead of an error."""
    target = _repo(tmp_path, "toy-a", origin="file:///origins/toy-a.git")
    elsewhere = _repo(tmp_path, "toy-b", origin="file:///origins/toy-b.git")
    monkeypatch.chdir(elsewhere)

    assert env_git().origin_url(target) == "file:///origins/toy-a.git"
