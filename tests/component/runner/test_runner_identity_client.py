"""The identity client a runner's composition root builds, owns and closes; the watch it feeds."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest

import blizzard.runner.composition as composition
from blizzard.runner.composition import HostProcess, add_runner, build_host_process
from blizzard.runner.config import RunnerConfig
from blizzard.runner.host_config import BuiltRunner, ResolvedToken, TokenSource
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.held_token import HubTokenHolder
from blizzard.runner.hub.identity import RunnerIdentity
from tests.support.host_files import host_config_of
from tests.support.runner_fakes import migrated_store_at

_RUNNER_ID = "rn_a"


def _config(tmp_path: Path) -> RunnerConfig:
    config = RunnerConfig(
        root=tmp_path, db_url=HostLayout(tmp_path).default_db_url, hub_url="http://hub.test", hub_token="old"
    )
    config.data_dir.mkdir(parents=True, exist_ok=True)
    migrated_store_at(config.db_url)
    return config


def _host(config: RunnerConfig, *, token: ResolvedToken | None = None) -> HostProcess:
    built = BuiltRunner(_RUNNER_ID, dataclasses.replace(config, runner_id=_RUNNER_ID), token or ResolvedToken())
    return build_host_process(host_config_of(config), [built])


def _retired_answer(request: httpx2.Request, seen: list[str]) -> httpx2.Response:
    seen.append(request.headers.get("authorization", ""))
    return httpx2.Response(
        401,
        json={
            "type": "urn:blizzard:problem:runner-identity",
            "title": "Denied",
            "detail": "retired",
            "instance": "/",
            "reason": "retired",
            "runner_id": "rn_b",
        },
    )


def test_the_watch_reads_and_forgets_through_the_runners_own_identity_store(tmp_path: Path) -> None:
    host = _host(_config(tmp_path))
    try:
        [runner] = host.runners
        watch = runner.retirement
        assert watch._rows is runner.stores.identity  # pyright: ignore[reportPrivateUsage]
        assert watch._forgetter is runner.stores.identity  # pyright: ignore[reportPrivateUsage]
    finally:
        host.close()


def test_one_instrumented_client_serves_every_identity_call_and_presents_the_re_read_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (tmp_path / ".env").write_text(f"{config.token_env}=old\n")
    seen: list[str] = []
    built: list[httpx2.Client] = []
    instrumented: list[httpx2.Client] = []
    real_client = httpx2.Client

    def recording_client(*args: object, **kwargs: object) -> httpx2.Client:
        kwargs["transport"] = httpx2.MockTransport(lambda request: _retired_answer(request, seen))
        client = real_client(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        built.append(client)
        return client

    monkeypatch.setattr(composition.httpx2, "Client", recording_client)
    real_held = composition.held_hub_token

    def dotenv_held(cfg: RunnerConfig, _token: ResolvedToken | None = None) -> HubTokenHolder:
        return real_held(cfg, ResolvedToken("old", TokenSource.DOTENV))

    monkeypatch.setattr(composition, "held_hub_token", dotenv_held)
    host = _host(config)
    try:
        monkeypatch.setattr(host.platform_tracing, "instrument_client", instrumented.append)
        built.clear()
        add_runner(
            dataclasses.replace(config, runner_id="rn_b"),
            host,
            runner_id="rn_b",
        )
        runner = host.runners[-1]
        assert built == [runner.identity_client] == instrumented
        record = RunnerIdentity(runner_id="rn_b", runner_name="b", registered_at=datetime(2026, 1, 1, tzinfo=UTC))
        runner.stores.identity.record_runner_identity(record)
        assert runner.retirement.forget_if_retired() is True
        runner.stores.identity.record_runner_identity(record)
        (tmp_path / ".env").write_text(f"{config.token_env}=new\n")
        runner.hub_token.reread()
        assert runner.retirement.forget_if_retired() is True
        assert seen == ["Bearer old", "Bearer new"]
        assert built == [runner.identity_client]
    finally:
        host.close()


def test_a_hosts_close_and_a_runners_close_each_close_its_identity_client(tmp_path: Path) -> None:
    config = _config(tmp_path)
    host = _host(config)
    add_runner(dataclasses.replace(config, runner_id="rn_b"), host, runner_id="rn_b")
    clients = [runner.identity_client for runner in host.runners]
    assert len(clients) == 2 and all(not client.is_closed for client in clients)
    host.close()
    assert all(client.is_closed for client in clients)


def test_closing_a_runner_twice_closes_its_client_once_in_effect(tmp_path: Path) -> None:
    host = _host(_config(tmp_path))
    [runner] = host.runners
    runner.close_runner()
    assert runner.identity_client.is_closed
    runner.close_runner()
    host.close()


def test_a_build_that_fails_after_the_client_opens_closes_it_and_leaves_the_hosts_resources_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    host = _host(config)
    opened: list[httpx2.Client] = []
    real_client = httpx2.Client

    def recording_client(*args: object, **kwargs: object) -> httpx2.Client:
        client = real_client(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        opened.append(client)
        return client

    def failing(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("build failed after the client opened")

    monkeypatch.setattr(composition.httpx2, "Client", recording_client)
    monkeypatch.setattr(composition, "RetiredRunnerWatch", failing)
    try:
        with pytest.raises(RuntimeError, match="build failed"):
            add_runner(dataclasses.replace(config, runner_id="rn_b"), host, runner_id="rn_b")
        assert opened and all(client.is_closed for client in opened)
        assert not any(runner.identity_client.is_closed for runner in host.runners)
        with host.engine.connect():
            pass
    finally:
        host.close()
