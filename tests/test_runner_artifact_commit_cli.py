"""``blizzard runner artifact commit`` — the verb's identity handling and rejection
surfacing (unit tier, issue #143 Phase 3): ``httpx.post`` stubbed, no live socket. Like
``artifact create``, this does not soft-fail: a rejection must reach the worker as a
non-zero exit. Carries no ``--forge`` — the origin comes from the repo manifest.
"""

from __future__ import annotations

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

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float, **_: object) -> _FakeResponse:
        calls.append((url, json, headers))
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        runner_group,
        ["artifact", "commit", "--repo", "blizzard", "--branch", "feat/x", "--commit", "abc123"],
        env=_ENV,
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "http://127.0.0.1:8431/api/leases/lease_9/git-commits",
            {"repo": "blizzard", "branch": "feat/x", "commit": "abc123"},
            {"X-Blizzard-Lease-Token": "the-lease-token"},
        )
    ]


def test_commit_verb_omits_the_environment_key_when_not_named(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``--env`` sends no ``environment_id`` key at all, rather than an explicit null."""
    calls: list[dict] = []

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float, **_: object) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    CliRunner().invoke(
        runner_group,
        ["artifact", "commit", "--repo", "r", "--branch", "b", "--commit", "c"],
        env=_ENV,
    )

    assert "environment_id" not in calls[0]


def test_commit_verb_forwards_the_named_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float, **_: object) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        runner_group,
        ["artifact", "commit", "--env", "r2", "--repo", "r", "--branch", "b", "--commit", "c"],
        env=_ENV,
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["environment_id"] == "r2"


def test_commit_verb_omits_the_token_header_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float, **_: object) -> _FakeResponse:
        calls.append(headers)
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    env = {"BLIZZARD_LEASE_ID": "lease_9", "BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/"}
    result = CliRunner().invoke(
        runner_group,
        ["artifact", "commit", "--repo", "r", "--branch", "b", "--commit", "c"],
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
        ["artifact", "commit", "--repo", "r", "--branch", "b", "--commit", "c"],
        env=env,
    )

    assert result.exit_code != 0  # unlike the hooks, artifact commit must not soft-fail
    assert posted is False


def test_commit_verb_surfaces_a_transport_failure_as_a_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable runner must reach the worker, not be swallowed."""

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        runner_group,
        ["artifact", "commit", "--repo", "r", "--branch", "b", "--commit", "c"],
        env=_ENV,
    )

    assert result.exit_code != 0
    assert "could not record" in result.output


def test_commit_verb_surfaces_the_rejection_detail_so_the_worker_can_correct_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 400 naming an unknown repo carries the env's actual repo list in its body. That
    guidance is the whole point of rejecting at declare time rather than dropping the
    declaration later, so it must survive to the worker's terminal."""

    class _RejectingResponse:
        status_code = 400

        def json(self) -> dict:
            return {"detail": "environment 'e1' has no repo 'blizzrd' — it holds ['blizzard', 'blizzard-mock']"}

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError("400", request=object(), response=self)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _RejectingResponse())
    result = CliRunner().invoke(
        runner_group,
        ["artifact", "commit", "--repo", "blizzrd", "--branch", "b", "--commit", "c"],
        env=_ENV,
    )

    assert result.exit_code != 0
    assert "blizzard-mock" in result.output


def test_commit_verb_requires_repo_branch_and_commit() -> None:
    result = CliRunner().invoke(runner_group, ["artifact", "commit", "--repo", "r"], env=_ENV)

    assert result.exit_code != 0
    assert "Missing option" in result.output


def test_commit_verb_has_no_forge_flag() -> None:
    """Structural pin: the flag is gone, not merely unused. Re-adding a worker-supplied
    forge re-opens the mismatch class the manifest lookup closed."""
    result = CliRunner().invoke(
        runner_group,
        ["artifact", "commit", "--forge", "github", "--repo", "r", "--branch", "b", "--commit", "c"],
        env=_ENV,
    )

    assert result.exit_code != 0
    assert "no such option" in result.output.lower()


@pytest.mark.unit
def test_commit_verb_refuses_graph_scope_without_posting(monkeypatch: pytest.MonkeyPatch) -> None:
    """A git-commit declaration is node-scoped by nature — ``--scope graph`` refuses
    before ever reaching the network, rather than posting a declaration nothing baked into
    the graph mint could ever hold."""
    posted = False

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal posted
        posted = True
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        runner_group,
        ["artifact", "commit", "--repo", "blizzard", "--branch", "feat/x", "--commit", "abc123", "--scope", "graph"],
        env=_ENV,
    )

    assert result.exit_code != 0
    assert "read-only" in result.output
    assert posted is False


@pytest.mark.unit
def test_commit_verb_refuses_system_scope_without_posting(monkeypatch: pytest.MonkeyPatch) -> None:
    """A system artifact is blizzard's own published document — ``--scope system`` refuses
    the same way ``--scope graph`` does, naming the scope."""
    posted = False

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal posted
        posted = True
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        runner_group,
        ["artifact", "commit", "--repo", "blizzard", "--branch", "feat/x", "--commit", "abc123", "--scope", "system"],
        env=_ENV,
    )

    assert result.exit_code != 0
    assert "read-only" in result.output and "system" in result.output
    assert posted is False
