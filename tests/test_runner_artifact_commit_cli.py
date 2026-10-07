"""``blizzard runner artifact commit`` — the verb's identity handling and rejection
surfacing (unit tier): ``httpx.post`` stubbed, no live socket. Like
``artifact create``, this does not soft-fail: a rejection must reach the worker as a
non-zero exit. Carries no ``--forge`` — the origin comes from the repo manifest.
"""

from __future__ import annotations

import httpx
import pytest
from click.testing import CliRunner

from blizzard.runner.cli import runner as runner_group
from tests.worker_http import bind_stubs

_ENV = {
    "BLIZZARD_LEASE_ID": "lease_9",
    "BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/",
    "BLIZZARD_LEASE_TOKEN": "the-lease-token",
}


class _FakeResponse:
    def __init__(self, payload: dict | None = None) -> None:
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


@pytest.mark.unit
def test_commit_verb_posts_inherited_identity_and_declaration_body(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict, dict]] = []

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float, **_: object) -> _FakeResponse:
        calls.append((url, json, headers))
        return _FakeResponse()

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
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


@pytest.mark.unit
def test_commit_verb_echoes_a_confirmation_naming_repo_branch_and_sha() -> None:
    """A silent exit 0 was indistinguishable from a no-op — the worker must see
    what was recorded without a second call."""
    bound = bind_stubs(post=lambda *a, **k: _FakeResponse())
    result = bound.runner.invoke(
        runner_group,
        ["artifact", "commit", "--repo", "blizzard", "--branch", "feat/x", "--commit", "abc123"],
        env=_ENV,
    )

    assert result.exit_code == 0, result.output
    assert "blizzard" in result.output
    assert "feat/x" in result.output
    assert "abc123" in result.output


@pytest.mark.unit
def test_commit_verb_echoes_the_response_note() -> None:
    bound = bind_stubs(post=lambda *a, **k: _FakeResponse({"note": "rides no completion"}))
    result = bound.runner.invoke(
        runner_group,
        ["artifact", "commit", "--repo", "blizzard", "--branch", "feat/x", "--commit", "abc123"],
        env=_ENV,
    )

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[-1] == "rides no completion"


@pytest.mark.unit
def test_commit_verb_omits_the_environment_key_when_not_named(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``--env`` sends no ``environment_id`` key at all, rather than an explicit null."""
    calls: list[dict] = []

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float, **_: object) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse()

    bound = bind_stubs(post=fake_post)
    bound.runner.invoke(
        runner_group,
        ["artifact", "commit", "--repo", "r", "--branch", "b", "--commit", "c"],
        env=_ENV,
    )

    assert "environment_id" not in calls[0]


@pytest.mark.unit
def test_commit_verb_forwards_the_named_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float, **_: object) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse()

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        runner_group,
        ["artifact", "commit", "--env", "r2", "--repo", "r", "--branch", "b", "--commit", "c"],
        env=_ENV,
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["environment_id"] == "r2"


@pytest.mark.unit
def test_commit_verb_omits_the_token_header_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float, **_: object) -> _FakeResponse:
        calls.append(headers)
        return _FakeResponse()

    bound = bind_stubs(post=fake_post)
    env = {"BLIZZARD_LEASE_ID": "lease_9", "BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/"}
    result = bound.runner.invoke(
        runner_group,
        ["artifact", "commit", "--repo", "r", "--branch", "b", "--commit", "c"],
        env=env,
    )

    assert result.exit_code == 0, result.output
    assert calls == [{}]


@pytest.mark.unit
def test_commit_verb_raises_without_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    posted = False

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal posted
        posted = True
        return _FakeResponse()

    bound = bind_stubs(post=fake_post)
    env = {"BLIZZARD_LEASE_ID": "", "BLIZZARD_RUNNER_URL": ""}
    result = bound.runner.invoke(
        runner_group,
        ["artifact", "commit", "--repo", "r", "--branch", "b", "--commit", "c"],
        env=env,
    )

    assert result.exit_code != 0  # unlike the hooks, artifact commit must not soft-fail
    assert posted is False


@pytest.mark.unit
def test_commit_verb_surfaces_a_transport_failure_as_a_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable runner must reach the worker, not be swallowed."""

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        raise httpx.ConnectError("connection refused")

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        runner_group,
        ["artifact", "commit", "--repo", "r", "--branch", "b", "--commit", "c"],
        env=_ENV,
    )

    assert result.exit_code != 0
    assert "could not record" in result.output


@pytest.mark.unit
def test_commit_verb_surfaces_the_rejection_detail_so_the_worker_can_correct_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 400's detail text reaches the worker's terminal, with a nonzero exit."""

    class _RejectingResponse:
        status_code = 400

        def json(self) -> dict:
            return {"detail": "environment 'e1' has no repo 'blizzrd' — it holds ['blizzard', 'blizzard-mock']"}

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError("400", request=object(), response=self)  # type: ignore[arg-type]

    bound = bind_stubs(post=lambda *a, **k: _RejectingResponse())
    result = bound.runner.invoke(
        runner_group,
        ["artifact", "commit", "--repo", "blizzrd", "--branch", "b", "--commit", "c"],
        env=_ENV,
    )

    assert result.exit_code != 0
    assert "blizzard-mock" in result.output


@pytest.mark.unit
def test_commit_verb_requires_repo_branch_and_commit() -> None:
    result = CliRunner().invoke(runner_group, ["artifact", "commit", "--repo", "r"], env=_ENV)

    assert result.exit_code != 0
    assert "Missing option" in result.output


@pytest.mark.unit
def test_commit_verb_has_no_forge_flag() -> None:
    """``--forge`` is not an option of the verb: passing it is a CLI usage error."""
    result = CliRunner().invoke(
        runner_group,
        ["artifact", "commit", "--forge", "github", "--repo", "r", "--branch", "b", "--commit", "c"],
        env=_ENV,
    )

    assert result.exit_code != 0
    assert "no such option" in result.output.lower()


@pytest.mark.unit
def test_commit_verb_refuses_graph_scope_without_posting(monkeypatch: pytest.MonkeyPatch) -> None:
    """A git-commit declaration is node-scoped: ``--scope graph`` refuses before any post."""
    posted = False

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal posted
        posted = True
        return _FakeResponse()

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
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

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        runner_group,
        ["artifact", "commit", "--repo", "blizzard", "--branch", "feat/x", "--commit", "abc123", "--scope", "system"],
        env=_ENV,
    )

    assert result.exit_code != 0
    assert "read-only" in result.output and "system" in result.output
    assert posted is False
