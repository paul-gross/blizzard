"""The ``blizzard runner heartbeat`` verb.

Covers the verb's identity handling and soft-fail (``httpx2.post`` stubbed)."""

from __future__ import annotations

import httpx2
import pytest

from blizzard.runner.cli import runner as runner_group
from tests.support.worker_http import bind_stubs


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None


def test_heartbeat_verb_posts_inherited_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """The verb reads the spawn-injected identity and posts it — no arguments."""
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_post(url: str, *, json: dict[str, object], timeout: float, **_: object) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse()

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        runner_group,
        ["heartbeat"],
        env={"BLIZZARD_LEASE_ID": "lease_9", "BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/"},
    )

    assert result.exit_code == 0, result.output
    assert calls == [("http://127.0.0.1:8431/api/heartbeat", {"lease_id": "lease_9"})]


def test_heartbeat_verb_soft_fails_without_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without identity the verb skips cleanly: exit 0 and no post attempted."""
    posted = False

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal posted
        posted = True
        return _FakeResponse()

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(runner_group, ["heartbeat"], env={"BLIZZARD_LEASE_ID": "", "BLIZZARD_RUNNER_URL": ""})

    assert result.exit_code == 0  # soft-fail, never raise
    assert "skipping" in result.output
    assert posted is False  # never even attempted the post


def test_heartbeat_verb_soft_fails_when_runner_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable runner is reported and swallowed — exit 0, tool call unbroken."""

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        raise httpx2.ConnectError("connection refused")

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        runner_group,
        ["heartbeat"],
        env={"BLIZZARD_LEASE_ID": "lease_9", "BLIZZARD_RUNNER_URL": "http://127.0.0.1:1/"},
    )

    assert result.exit_code == 0
    assert "could not reach the runner" in result.output
