"""The worker session-end path — local-API endpoint + ``blizzard runner session-end`` verb.

Component exercises the endpoint over a real store (TestClient); unit exercises the
verb's identity handling and soft-fail. A third block asserts the settings document
actually wires the hook.
"""

from __future__ import annotations

import httpx2
import pytest

from blizzard.runner.cli import runner as runner_group
from blizzard.runner.harness.claude_code.worker_settings import WorkerSettings
from blizzard.runner.harness.worker_hooks import SESSION_END_HOOK_COMMAND
from tests.worker_http import bind_stubs


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None


def test_session_end_verb_posts_inherited_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """The verb reads the spawn-injected identity and posts to the lease-scoped route — no args."""
    calls: list[str] = []

    def fake_post(url: str, *, timeout: float, **_: object) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse()

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        runner_group,
        ["session-end"],
        env={"BLIZZARD_LEASE_ID": "lease_9", "BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/"},
    )

    assert result.exit_code == 0, result.output
    assert calls == ["http://127.0.0.1:8431/api/leases/lease_9/session-end"]


def test_session_end_verb_skips_on_elicitation_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """A judge/nudge resume's ``SessionEnd`` fire must record no done-signal for the lease."""
    calls: list[str] = []

    def fake_post(url: str, *, timeout: float, **_: object) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse()

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        runner_group,
        ["session-end"],
        env={
            "BLIZZARD_LEASE_ID": "lease_9",
            "BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/",
            "BLIZZARD_ELICITATION": "1",
        },
    )

    assert result.exit_code == 0, result.output
    assert calls == []


def test_session_end_verb_soft_fails_without_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """No identity means a clean skip: exit 0, and no post attempted."""
    posted = False

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal posted
        posted = True
        return _FakeResponse()

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        runner_group, ["session-end"], env={"BLIZZARD_LEASE_ID": "", "BLIZZARD_RUNNER_URL": ""}
    )

    assert result.exit_code == 0  # soft-fail, never raise
    assert "skipping" in result.output
    assert posted is False  # never even attempted the post


def test_session_end_verb_soft_fails_when_runner_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable runner is reported and swallowed — exit 0."""

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        raise httpx2.ConnectError("connection refused")

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        runner_group,
        ["session-end"],
        env={"BLIZZARD_LEASE_ID": "lease_9", "BLIZZARD_RUNNER_URL": "http://127.0.0.1:1/"},
    )

    assert result.exit_code == 0
    assert "could not reach the runner" in result.output


def test_worker_settings_wires_the_session_end_hook() -> None:
    """The settings file the adapter passes to ``claude -p`` fires the session-end verb on exit."""
    hooks = WorkerSettings.of().document["hooks"]
    commands = [h["command"] for entry in hooks["SessionEnd"] for h in entry["hooks"]]
    assert commands == [SESSION_END_HOOK_COMMAND] == ["blizzard runner session-end"]
