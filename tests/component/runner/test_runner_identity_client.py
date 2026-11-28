"""The identity client a runner's composition root builds, owns and closes; the watch it feeds."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest

import blizzard.runner.composition as composition
from blizzard.foundation.secrets.value import SecretValue
from blizzard.runner.composition import HostProcess, RunnerStartFailed
from blizzard.runner.config import RunnerConfig
from blizzard.runner.host_config import BuiltRunner
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hosting.retirement import HubNoticedRetirement
from blizzard.runner.hub.identity import RunnerIdentity
from tests.support.composed_host import build_host_process, serving_root, start_on
from tests.support.host_files import host_config_of
from tests.support.hub_tokens import host_hub_tokens
from tests.support.runner_fakes import migrated_store_at

_RUNNER_ID = "rn_a"


def _config(tmp_path: Path) -> RunnerConfig:
    config = RunnerConfig(
        root=tmp_path, db_url=HostLayout(tmp_path).default_db_url, hub_url="http://hub.test", hub_token="old"
    )
    config.data_dir.mkdir(parents=True, exist_ok=True)
    migrated_store_at(config.db_url)
    return config


def _host(config: RunnerConfig) -> HostProcess:
    built = BuiltRunner(_RUNNER_ID, dataclasses.replace(config, runner_id=_RUNNER_ID))
    return build_host_process(host_config_of(config), [built])


def _second(config: RunnerConfig, **changes: object) -> BuiltRunner:
    return BuiltRunner("rn_b", dataclasses.replace(config, runner_id="rn_b", **changes))


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


def test_the_watch_reads_the_runners_own_identity_store_and_records_into_the_hosts_hosted_runners(
    tmp_path: Path,
) -> None:
    host = _host(_config(tmp_path))
    try:
        [runner] = host.runners
        watch = runner.retirement
        assert watch._rows is runner.stores.identity  # pyright: ignore[reportPrivateUsage]
        retirement = watch._retirement  # pyright: ignore[reportPrivateUsage]
        assert isinstance(retirement, HubNoticedRetirement)
        assert retirement.hosted is host.host_stores.hosted_runners
        assert retirement.notices is host.retirements
    finally:
        host.close()


def test_one_instrumented_client_serves_every_identity_call_and_presents_the_replaced_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    with host_hub_tokens(config.root, config.db_url) as tokens:
        tokens.seal_if_absent("rn_b", SecretValue("old"))
    seen: list[str] = []
    built: list[httpx2.Client] = []
    instrumented: list[httpx2.Client] = []
    real_client = httpx2.Client

    def recording_client(*args: object, **kwargs: object) -> httpx2.Client:
        kwargs["transport"] = httpx2.MockTransport(lambda request: _retired_answer(request, seen))
        client = real_client(*args, **kwargs)
        built.append(client)
        return client

    monkeypatch.setattr(composition.httpx2, "Client", recording_client)
    host = _host(config)
    root = serving_root(host_config_of(config), host)
    try:
        monkeypatch.setattr(host.platform_tracing, "instrument_client", instrumented.append)
        built.clear()
        runner = start_on(root, _second(config, hub_token=""))
        assert host.runners[-1] is runner
        assert built == [runner.identity_client] == instrumented
        record = RunnerIdentity(runner_id="rn_b", runner_name="b", registered_at=datetime(2026, 1, 1, tzinfo=UTC))
        runner.stores.identity.record_runner_identity(record)
        assert runner.retirement.record_if_retired() is True
        with host_hub_tokens(config.root, config.db_url) as tokens:
            tokens.replace("rn_b", "new", verdict=None)
        runner.hub_token.refresh()
        assert runner.retirement.record_if_retired() is False
        assert seen == ["Bearer old", "Bearer new"]
        assert built == [runner.identity_client]
    finally:
        root.close()
        host.close()


def test_a_hosts_close_and_a_runners_close_each_close_its_identity_client(tmp_path: Path) -> None:
    config = _config(tmp_path)
    host = _host(config)
    root = serving_root(host_config_of(config), host)
    start_on(root, _second(config))
    clients = [runner.identity_client for runner in host.runners]
    assert len(clients) == 2 and all(not client.is_closed for client in clients)
    root.close()
    assert [client.is_closed for client in clients] == [False, True]
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
        client = real_client(*args, **kwargs)
        opened.append(client)
        return client

    def failing(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("build failed after the client opened")

    monkeypatch.setattr(composition.httpx2, "Client", recording_client)
    monkeypatch.setattr(composition, "RetiredRunnerWatch", failing)
    root = serving_root(host_config_of(config), host)
    try:
        with pytest.raises(RunnerStartFailed, match="build failed"):
            start_on(root, _second(config))
        assert opened and all(client.is_closed for client in opened)
        assert not any(runner.identity_client.is_closed for runner in host.runners)
        with host.engine.connect():
            pass
    finally:
        root.close()
        host.close()
