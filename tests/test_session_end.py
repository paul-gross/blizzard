"""The worker session-end path — local-API endpoint + ``blizzard runner session-end`` verb.

A worker's ``SessionEnd`` hook runs ``blizzard runner session-end`` when its Claude session
exits (design/harness-adapters.md): the verb — a pure client of the runner's local API
(D-023) — posts to ``POST /api/leases/{lease_id}/session-end`` for the lease it inherited from
the spawn environment (``BLIZZARD_LEASE_ID`` / ``BLIZZARD_RUNNER_URL``). The daemon appends the
"declared done" fact (exit-is-done, D-055), and startup crash-recovery reads its *absence* to
tell a worker killed mid-work from one that cleanly exited (D-082).

Mirrors ``test_heartbeat.py`` — the same two tiers with no live socket: **component** exercises
the endpoint over a real store (TestClient); **unit** exercises the verb's identity handling and
soft-fail (``httpx.post`` stubbed), since the mock ``mock-claude-code`` façade does not emulate
hooks. A third block asserts the settings document actually wires the hook.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from blizzard.runner.app import create_app
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.worker_settings import SESSION_END_HOOK_COMMAND, worker_settings_document
from tests.runner_fakes import make_store


def _runner_app_with_store(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A runner app wired to a real (migrated) store — the ``host`` session-end surface."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    return create_app(config, runner_store=store), store


# --------------------------------------------------------------------------- #
# The local-API endpoint (component tier)
# --------------------------------------------------------------------------- #


@pytest.mark.component
def test_session_end_endpoint_records_the_fact(tmp_path: Path) -> None:
    app, store = _runner_app_with_store(tmp_path)
    assert store.session_ended_lease_ids() == set()

    with TestClient(app) as client:
        resp = client.post("/api/leases/lease_1/session-end")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"recorded": True, "lease_id": "lease_1"}
    assert store.session_ended_lease_ids() == {"lease_1"}  # the "declared done" signal now exists


@pytest.mark.component
def test_session_end_endpoint_503_when_store_unwired(tmp_path: Path) -> None:
    """The store-free app (OpenAPI export / unit boot) answers 503, never pretends."""
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    with TestClient(create_app(config)) as client:
        resp = client.post("/api/leases/lease_1/session-end")
    assert resp.status_code == 503


# --------------------------------------------------------------------------- #
# The `blizzard runner session-end` verb (unit tier)
# --------------------------------------------------------------------------- #


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None


def test_session_end_verb_posts_inherited_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """The verb reads the spawn-injected identity and posts to the lease-scoped route — no args."""
    calls: list[str] = []

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        runner_group,
        ["session-end"],
        env={"BLIZZARD_LEASE_ID": "lease_9", "BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/"},
    )

    assert result.exit_code == 0, result.output
    assert calls == ["http://127.0.0.1:8431/api/leases/lease_9/session-end"]


def test_session_end_verb_soft_fails_without_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hook must never break the worker's exit — no identity means a clean skip."""
    posted = False

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal posted
        posted = True
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(runner_group, ["session-end"], env={"BLIZZARD_LEASE_ID": "", "BLIZZARD_RUNNER_URL": ""})

    assert result.exit_code == 0  # soft-fail, never raise
    assert "skipping" in result.output
    assert posted is False  # never even attempted the post


def test_session_end_verb_soft_fails_when_runner_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable runner is reported and swallowed — exit 0, the worker's exit unbroken."""

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        runner_group,
        ["session-end"],
        env={"BLIZZARD_LEASE_ID": "lease_9", "BLIZZARD_RUNNER_URL": "http://127.0.0.1:1/"},
    )

    assert result.exit_code == 0
    assert "could not reach the runner" in result.output


# --------------------------------------------------------------------------- #
# The worker settings document wires the hook
# --------------------------------------------------------------------------- #


def test_worker_settings_wires_the_session_end_hook() -> None:
    """The settings file the adapter passes to ``claude -p`` fires the session-end verb on exit."""
    hooks = worker_settings_document()["hooks"]
    commands = [h["command"] for entry in hooks["SessionEnd"] for h in entry["hooks"]]
    assert commands == [SESSION_END_HOOK_COMMAND] == ["blizzard runner session-end"]
