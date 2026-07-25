"""``blizzard runner artifact commit`` — the verb's identity handling and rejection
surfacing (unit tier, issue #143 Phase 3), mirroring ``tests/test_runner_attach_cli.py``:
``httpx.post`` stubbed, no live socket. The endpoint itself (store round-trip,
403/404/503) is the component tier's ``tests/test_runner_git_commits_api.py``.

Like ``artifact create``, this does not soft-fail: a rejection must reach the worker as
a non-zero exit so it learns the declaration was not durable.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from blizzard.runner.cli import runner as runner_group

_ENV = {
    "BLIZZARD_LEASE_ID": "lease_9",
    "BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/",
    "BLIZZARD_LEASE_TOKEN": "the-lease-token",
}


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None


def test_commit_verb_posts_inherited_identity_and_declaration_body(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict, dict]] = []

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float) -> _FakeResponse:
        calls.append((url, json, headers))
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        runner_group,
        [
            "artifact",
            "commit",
            "--forge",
            "github",
            "--repo",
            "blizzard",
            "--branch",
            "feat/x",
            "--commit",
            "abc123",
        ],
        env=_ENV,
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "http://127.0.0.1:8431/api/leases/lease_9/git-commits",
            {"forge": "github", "repo": "blizzard", "branch": "feat/x", "commit": "abc123"},
            {"X-Blizzard-Lease-Token": "the-lease-token"},
        )
    ]


def test_commit_verb_omits_the_token_header_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float) -> _FakeResponse:
        calls.append(headers)
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    env = {"BLIZZARD_LEASE_ID": "lease_9", "BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/"}
    result = CliRunner().invoke(
        runner_group,
        ["artifact", "commit", "--forge", "github", "--repo", "r", "--branch", "b", "--commit", "c"],
        env=env,
    )

    assert result.exit_code == 0, result.output
    assert calls == [{}]


def test_commit_verb_raises_without_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    posted = False

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal posted
        posted = True
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    env = {"BLIZZARD_LEASE_ID": "", "BLIZZARD_RUNNER_URL": ""}
    result = CliRunner().invoke(
        runner_group,
        ["artifact", "commit", "--forge", "github", "--repo", "r", "--branch", "b", "--commit", "c"],
        env=env,
    )

    assert result.exit_code != 0  # unlike the hooks, artifact commit must not soft-fail
    assert posted is False


def test_commit_verb_surfaces_a_rejection_as_a_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 403 (wrong/missing token) must reach the worker, not be swallowed."""

    class _RejectingResponse:
        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError("403 forbidden", request=object(), response=object())  # type: ignore[arg-type]

    def fake_post(*args: object, **kwargs: object) -> _RejectingResponse:
        return _RejectingResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        runner_group,
        ["artifact", "commit", "--forge", "github", "--repo", "r", "--branch", "b", "--commit", "c"],
        env=_ENV,
    )

    assert result.exit_code != 0
    assert "could not record" in result.output


def test_commit_verb_requires_all_four_flags() -> None:
    result = CliRunner().invoke(runner_group, ["artifact", "commit", "--forge", "github"], env=_ENV)

    assert result.exit_code != 0
    assert "Missing option" in result.output


def test_commit_verb_defaults_forge_to_the_cwd_repos_own_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--forge`` omitted declares the ``origin`` `git remote get-url` observes in the
    current directory (issue #143 pre-push review) — the common case needs no flag."""
    repo = tmp_path
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:org/blizzard.git"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    calls: list[dict] = []

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(
        runner_group,
        ["artifact", "commit", "--repo", "blizzard", "--branch", "feat/x", "--commit", "abc123"],
        env=_ENV,
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        {"forge": "git@github.com:org/blizzard.git", "repo": "blizzard", "branch": "feat/x", "commit": "abc123"}
    ]


def test_commit_verb_raises_when_forge_omitted_and_cwd_has_no_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)

    posted = False

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal posted
        posted = True
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        runner_group,
        ["artifact", "commit", "--repo", "blizzard", "--branch", "feat/x", "--commit", "abc123"],
        env=_ENV,
    )

    assert result.exit_code != 0
    assert posted is False
