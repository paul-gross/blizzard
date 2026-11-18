"""``blizzard runner token set``: the token is read from stdin only and never printed. A stopped host seals it in the
store under the store write guard, refusing a store its runner is foreign to; a running host seals it over its
socket, where the runner takes it up at its next identity check."""

from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
import uvicorn
from click.testing import CliRunner

from blizzard.runner.app import build_hosted_app
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.cli.runtime import seed_runners
from blizzard.runner.composition import build_host_process
from blizzard.runner.host_config import HostConfig
from blizzard.runner.host_keys import ENV_HOST_SECRET_KEY
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.hub.sealed_tokens import StoredToken
from blizzard.runner.listeners import Listeners, Uds
from tests.support.config_inputs import load_host_config
from tests.support.hub_tokens import host_hub_tokens
from tests.support.runner_cli_home import (
    _await_socket,  # pyright: ignore[reportPrivateUsage]
)
from tests.support.runner_fakes import make_store, migrated_store_at

_RUNNER = "rn_one"
_NEW = "tok-new-value"


def _host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HostConfig:
    monkeypatch.delenv(ENV_HOST_SECRET_KEY, raising=False)
    monkeypatch.setenv("BZ_HUB_TOKEN_R_ONE", "tok-old")
    db_url = f"sqlite:///{tmp_path / 'host.db'}"
    migrated_store_at(db_url)
    HostLayout(tmp_path).config_path.write_text(
        f'db_url = "{db_url}"\nhub_url = "http://127.0.0.1:9"\n\n[[workspace]]\nid = "workspace-local"\n\n'
        f'[[runner]]\nid = "{_RUNNER}"\nname = "r-one"\nprojects = []\n'
    )
    return load_host_config(tmp_path)


def _set(root: Path, token: str) -> tuple[int, str]:
    result = CliRunner().invoke(runner_group, ["token", "set", "--runner", "r-one", "--dir", str(root)], input=token)
    return result.exit_code, result.output


def test_a_stopped_host_seals_the_token_under_the_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _host(tmp_path, monkeypatch)

    code, output = _set(tmp_path, f"{_NEW}\n")

    assert code == 0, output
    assert "runner r-one (rn_one): hub token set in the host store at revision 1" in output
    assert "next start" in output and _NEW not in output
    assert (tmp_path / "data" / "secret-keys").is_dir()
    with host_hub_tokens(config.root, config.db_url) as tokens:
        assert tokens.stored(_RUNNER) == StoredToken.opened(_NEW, 1)


def test_an_empty_token_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _host(tmp_path, monkeypatch)

    code, output = _set(tmp_path, "\n")

    assert code != 0
    assert "refusing an empty token" in output


def test_a_stopped_host_refuses_a_store_its_runner_is_foreign_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _host(tmp_path, monkeypatch)
    make_store(config.db_url, "rn_other").record_runner_identity(
        RunnerIdentity("rn_other", "other", datetime(2026, 7, 16, tzinfo=UTC))
    )

    code, output = _set(tmp_path, _NEW)

    assert code != 0, output
    assert "runner r-one (rn_one)" in output
    with host_hub_tokens(config.root, config.db_url) as tokens:
        assert tokens.stored(_RUNNER) == StoredToken.absent()


@contextmanager
def _serving(config: HostConfig) -> Generator[None]:
    ready, halted = seed_runners(config, list(config.runners))
    assert not halted
    graph = build_host_process(config, ready)
    served = replace(ready[0].config, port=0)
    hosted = build_hosted_app(served, host=graph)
    sockets = Listeners.of(served).bound()
    server = uvicorn.Server(uvicorn.Config(hosted.app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run(sockets=sockets), daemon=True)
    thread.start()
    try:
        _await_socket(served.socket_path)
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)
        Uds(served.socket_path).unlink()
        hosted.close()
        graph.close()


def test_a_running_host_seals_the_token_over_its_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _host(tmp_path, monkeypatch)
    with _serving(config):
        code, output = _set(tmp_path, _NEW)

    assert code == 0, output
    assert "runner r-one (rn_one): hub token set in the host store; it takes effect at its next identity check" in (
        output
    )
    assert _NEW not in output
    with host_hub_tokens(config.root, config.db_url) as tokens:
        assert tokens.stored(_RUNNER) == StoredToken.opened(_NEW, 2)
