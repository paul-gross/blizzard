"""The worker heartbeat path — local-API endpoint + ``blizzard runner heartbeat`` verb.

A worker heartbeats as a side effect of working (design/harness-adapters.md): its
``PostToolUse`` hook runs ``blizzard runner heartbeat`` on every tool call, and the
verb — a pure client of the runner's local API (D-023) — posts to ``POST
/api/heartbeat`` for the lease it inherited from the spawn environment
(``BLIZZARD_LEASE_ID`` / ``BLIZZARD_RUNNER_URL``). The daemon appends the beat to its
store, and REAP reads the last beat to catch a stalled-but-alive worker.

Two tiers, no live socket:

* **component** — the endpoint over a real store (TestClient), the API + store half;
* **unit** — the verb's identity handling and soft-fail (``httpx.post`` stubbed), the
  CLI half. The mock ``mock-claude-code`` façade accepts ``--settings`` but does not
  emulate hooks, so the verb is exercised directly here rather than through a hook.
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
from tests.runner_fakes import make_store


def _runner_app_with_store(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A runner app wired to a real (migrated) store — the ``host`` heartbeat surface."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    return create_app(config, runner_store=store), store


# --------------------------------------------------------------------------- #
# The local-API endpoint (component tier)
# --------------------------------------------------------------------------- #


@pytest.mark.component
def test_heartbeat_endpoint_records_a_beat(tmp_path: Path) -> None:
    app, store = _runner_app_with_store(tmp_path)
    assert store.latest_heartbeat("lease_1") is None

    with TestClient(app) as client:
        resp = client.post("/api/heartbeat", json={"lease_id": "lease_1"})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"recorded": True, "lease_id": "lease_1"}
    assert store.latest_heartbeat("lease_1") is not None  # REAP's stall signal now exists


@pytest.mark.component
def test_heartbeat_endpoint_503_when_store_unwired(tmp_path: Path) -> None:
    """The store-free app (OpenAPI export / unit boot) answers 503, never pretends."""
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    with TestClient(create_app(config)) as client:
        resp = client.post("/api/heartbeat", json={"lease_id": "lease_1"})
    assert resp.status_code == 503


# --------------------------------------------------------------------------- #
# The `blizzard runner heartbeat` verb (unit tier)
# --------------------------------------------------------------------------- #


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None


def test_heartbeat_verb_posts_inherited_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """The verb reads the spawn-injected identity and posts it — no arguments."""
    calls: list[tuple[str, dict]] = []

    def fake_post(url: str, *, json: dict, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        runner_group,
        ["heartbeat"],
        env={"BLIZZARD_LEASE_ID": "lease_9", "BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/"},
    )

    assert result.exit_code == 0, result.output
    assert calls == [("http://127.0.0.1:8431/api/heartbeat", {"lease_id": "lease_9"})]


def test_heartbeat_verb_soft_fails_without_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hook must never break the worker's tool call — no identity means a clean skip."""
    posted = False

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal posted
        posted = True
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(runner_group, ["heartbeat"], env={"BLIZZARD_LEASE_ID": "", "BLIZZARD_RUNNER_URL": ""})

    assert result.exit_code == 0  # soft-fail, never raise
    assert "skipping" in result.output
    assert posted is False  # never even attempted the post


def test_heartbeat_verb_soft_fails_when_runner_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable runner is reported and swallowed — exit 0, tool call unbroken."""

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        runner_group,
        ["heartbeat"],
        env={"BLIZZARD_LEASE_ID": "lease_9", "BLIZZARD_RUNNER_URL": "http://127.0.0.1:1/"},
    )

    assert result.exit_code == 0
    assert "could not reach the runner" in result.output
