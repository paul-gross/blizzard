"""``PUT /api/host/runners/{runner_id}/token`` over a real host: the replaced token is sealed in the host store and the
runner takes it up at its next identity check, whatever its verdict; an unknown runner, a retired one, a blank value
and a non-superuser session are refused; and the value appears in no response or log record."""

from __future__ import annotations

import dataclasses
import shutil
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from blizzard.auth_core import Role
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.runner_identity_state import IdentityState
from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.runner.app import build_hosted_app
from blizzard.runner.auth.session import CookieNames, RunnerSession, SessionCookie
from blizzard.runner.cli.runtime import seed_runners
from blizzard.runner.composition import RunnerProcess
from blizzard.runner.host_config import HostConfig
from blizzard.runner.host_keys import ENV_HOST_SECRET_KEY
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.client import TokenIdentity, TokenRefusal
from blizzard.runner.hub.held_token import HubTokenHolder
from blizzard.runner.hub.sealed_tokens import StoredToken
from blizzard.runner.lifecycle.identity_check import IdentityCheck
from blizzard.runner.loop_wiring import RunnerStepper
from tests.support.composed_host import build_host_process
from tests.support.config_inputs import load_host_config
from tests.support.hub_tokens import host_hub_tokens
from tests.support.runner_fakes import migrated_store_at

_RUNNER = "rn_one"
_HALTED = "rn_halted"
_OLD = "tok-old"
_NEW = "tok-new-value"


class _PresentedTokenHub:
    """A hub answering the identity read for the token the runner presents: a known one names its runner, any other
    is refused for ``refusal``."""

    def __init__(self, held: HubTokenHolder, known: dict[str, str], refusal: RunnerTokenRefusalReason) -> None:
        self._held = held
        self._known = known
        self._refusal = refusal

    def identity(self) -> TokenIdentity | TokenRefusal:
        presented = self._held.auth_headers().get("Authorization", "").removeprefix("Bearer ")
        runner_id = self._known.get(presented)
        if runner_id is None:
            return TokenRefusal(reason=self._refusal, runner_id=_RUNNER)
        return TokenIdentity(runner_id=runner_id, runner_name="r-one")


def _host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HostConfig:
    """A host of one runner holding a token and one halted for holding none."""
    monkeypatch.delenv(ENV_HOST_SECRET_KEY, raising=False)
    monkeypatch.setenv("BZ_HUB_TOKEN_R_ONE", _OLD)
    db_url = f"sqlite:///{tmp_path / 'host.db'}"
    migrated_store_at(db_url)
    HostLayout(tmp_path).config_path.write_text(
        f'db_url = "{db_url}"\nhub_url = "http://127.0.0.1:9"\nhost_token = "tok-host"\n\n[[workspace]]\nid = "workspace-local"\n\n'
        f'[[runner]]\nid = "{_RUNNER}"\nname = "r-one"\nprojects = []\n\n'
        f'[[runner]]\nid = "{_HALTED}"\nname = "r-halted"\nprojects = []\n'
    )
    return load_host_config(tmp_path)


@contextmanager
def _served(config: HostConfig) -> Generator[tuple[TestClient, RunnerProcess]]:
    ready, halted = seed_runners(config, clock=SystemClock())
    assert [item.id for item in halted] == [_HALTED]
    graph = build_host_process(config, ready, halted=halted)
    hosted = build_hosted_app(ready[0].config, host=graph)
    hosted.app.state.hub_auth_mode = SimpleNamespace(enabled=lambda: False)
    try:
        with TestClient(hosted.app) as client:
            yield client, graph.runners[0]
    finally:
        hosted.close()
        graph.close()


def _check(runner: RunnerProcess, refusal: RunnerTokenRefusalReason = RunnerTokenRefusalReason.UNKNOWN) -> None:
    stepper = RunnerStepper(runner.config, process_graph=runner)
    try:
        ctx = stepper._context()  # pyright: ignore[reportPrivateUsage]
        hub = _PresentedTokenHub(runner.hub_token, {_OLD: _RUNNER, _NEW: _RUNNER}, refusal)
        IdentityCheck(dataclasses.replace(ctx, token_identity=hub)).run()
    finally:
        stepper.close()


def _put(client: TestClient, runner_id: str, token: str) -> int:
    return client.put(f"/api/host/runners/{runner_id}/token", json={"token": token}).status_code


@pytest.mark.parametrize("before", [IdentityState.REFUSED, IdentityState.MATCHED])
def test_a_replaced_token_is_taken_up_at_the_next_identity_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, before: IdentityState
) -> None:
    config = _host(tmp_path, monkeypatch)
    if before is IdentityState.REFUSED:
        monkeypatch.setenv("BZ_HUB_TOKEN_R_ONE", "tok-refused")
    with _served(config) as (client, runner):
        _check(runner)
        assert runner.identity_verdict.verdict().state is before

        assert _put(client, _RUNNER, _NEW) == 204
        _check(runner)

        assert runner.identity_verdict.verdict().state is IdentityState.MATCHED
        assert runner.hub_token.auth_headers() == {"Authorization": f"Bearer {_NEW}"}
    with host_hub_tokens(config.root, config.db_url) as tokens:
        assert tokens.stored(_RUNNER) == StoredToken.opened(_NEW, 2)


def test_a_lost_key_leaves_the_runner_refusing_until_its_token_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _host(tmp_path, monkeypatch)
    seed_runners(config, clock=SystemClock())
    shutil.rmtree(tmp_path / "data" / "secret-keys")
    with _served(load_host_config(tmp_path)) as (client, runner):
        _check(runner)
        assert runner.identity_verdict.verdict().state is IdentityState.REFUSED

        assert _put(client, _RUNNER, _NEW) == 204
        _check(runner)

        assert runner.identity_verdict.verdict().state is IdentityState.MATCHED


def test_the_route_refuses_unknown_retired_blank_and_never_echoes_or_logs_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _host(tmp_path, monkeypatch)
    monkeypatch.setenv("BZ_HUB_TOKEN_R_ONE", "tok-retired")
    with _served(config) as (client, runner), capture_logs() as logs:
        responses = [client.put("/api/host/runners/rn_nobody/token", json={"token": _NEW})]
        assert (responses[-1].status_code, responses[-1].json()["code"]) == (404, "runner_unknown")

        responses.append(client.put(f"/api/host/runners/{_RUNNER}/token", json={"token": "  "}))
        assert (responses[-1].status_code, responses[-1].json()["code"]) == (422, "token_blank")

        responses.append(client.put(f"/api/host/runners/{_HALTED}/token", json={"token": _NEW}))
        assert responses[-1].status_code == 204

        _check(runner, RunnerTokenRefusalReason.RETIRED)
        responses.append(client.put(f"/api/host/runners/{_RUNNER}/token", json={"token": _NEW}))
        assert (responses[-1].status_code, responses[-1].json()["code"]) == (409, "runner_retired")

        responses.append(
            client.put(
                f"/api/host/runners/{_HALTED}/token",
                json={"token": _NEW},
                headers={"X-Blizzard-Lease-Token": "worker-token"},
            )
        )
        assert responses[-1].status_code == 401

    assert all(_NEW not in response.text for response in responses)
    assert all(_NEW not in str(record) for record in logs)
    with host_hub_tokens(config.root, config.db_url) as tokens:
        assert tokens.stored(_HALTED) == StoredToken.opened(_NEW, 1)
        assert tokens.stored(_RUNNER) == StoredToken.opened("tok-retired", 1)


def test_a_non_superuser_session_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _host(tmp_path, monkeypatch)
    with _served(config) as (client, _):
        app = cast(FastAPI, client.app)
        app.state.hub_auth_mode = SimpleNamespace(enabled=lambda: True)
        names = CookieNames(_RUNNER)
        app.state.federation = SimpleNamespace(cookie_names=lambda: names)
        secret = b"test-hub-token-session-secret"
        app.state.session_secret = secret
        now = datetime.now(UTC)
        for role, expected in ((Role.CONTRIBUTOR, 403), (Role.SUPERUSER, 204)):
            client.cookies.set(
                names.session, SessionCookie(secret).mint(RunnerSession("alice", role, now, now + timedelta(hours=1)))
            )
            response = client.put(f"/api/host/runners/{_RUNNER}/token", json={"token": _NEW})
            assert response.status_code == expected, response.text
            if expected == 403:
                assert response.json()["code"] == "superuser_required"
                assert _NEW not in response.text
