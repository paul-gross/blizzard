"""The seed's token import: each runner's token is sealed in the host store from its deprecated sources at the first
start, read from the store from then on, and a row that does not open leaves its runner refusing on its own."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.runner_identity_state import IdentityState
from blizzard.runner.cli.runtime import seed_runners
from blizzard.runner.composition import build_host_process
from blizzard.runner.host_config import BuiltRunner, HostConfig
from blizzard.runner.host_keys import ENV_HOST_SECRET_KEY
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.sealed_tokens import StoredToken, StoredTokenState
from blizzard.runner.lifecycle.identity_check import IdentityCheck, TickScope, tick_scope
from blizzard.runner.loop_wiring import RunnerStepper
from tests.support.config_inputs import load_host_config
from tests.support.hub_tokens import host_hub_tokens
from tests.support.runner_fakes import migrated_store_at

_RUNNERS = {"rn_env": ("r-env", "tok-env"), "rn_dot": ("r-dot", "tok-dot"), "rn_file": ("r-file", "tok-file")}


def _host_text(db_url: str, *, dot_name: str = "r-dot") -> str:
    runners = "".join(
        f'\n[[runner]]\nid = "{runner_id}"\nname = "{dot_name if runner_id == "rn_dot" else name}"\nprojects = []\n'
        for runner_id, (name, _) in _RUNNERS.items()
    )
    return (
        f'db_url = "{db_url}"\nhub_url = "http://127.0.0.1:9"\nhost_token = "tok-host"\n\n[hub_tokens]\n"r-file" = "tok-file"\n\n'
        f'[[workspace]]\nid = "workspace-local"\n{runners}'
    )


def _upgraded_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HostConfig:
    """A host from before sealed tokens: one runner's token in the environment, one in ``.env``, one in
    ``[hub_tokens]``."""
    monkeypatch.delenv(ENV_HOST_SECRET_KEY, raising=False)
    monkeypatch.setenv("BZ_HUB_TOKEN_R_ENV", "tok-env")
    db_url = f"sqlite:///{tmp_path / 'host.db'}"
    migrated_store_at(db_url)
    HostLayout(tmp_path).config_path.write_text(_host_text(db_url))
    (tmp_path / ".env").write_text("BZ_HUB_TOKEN_R_DOT=tok-dot\n")
    return load_host_config(tmp_path)


def _stored(config: HostConfig) -> dict[str, StoredToken]:
    with host_hub_tokens(config.root, config.db_url) as tokens:
        return {runner_id: tokens.stored(runner_id) for runner_id in _RUNNERS}


def _seeded(config: HostConfig) -> dict[str, BuiltRunner]:
    ready, halted = seed_runners(config, list(config.runners), clock=SystemClock())
    assert halted == []
    return {built.id: built for built in ready}


def test_an_upgraded_host_finds_every_token_in_the_store_and_its_sources_are_never_read_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _upgraded_host(tmp_path, monkeypatch)

    built = _seeded(config)

    expected = {runner_id: StoredToken.opened(token, 1) for runner_id, (_, token) in _RUNNERS.items()}
    assert _stored(config) == expected
    assert {runner_id: runner.config.hub_token for runner_id, runner in built.items()} == {
        runner_id: token for runner_id, (_, token) in _RUNNERS.items()
    }
    assert (tmp_path / "data" / "secret-keys").stat().st_mode & 0o777 == 0o700
    capsys.readouterr()

    (tmp_path / ".env").write_text("BZ_HUB_TOKEN_R_DOT=tok-dot-edited\n")
    again = _seeded(load_host_config(tmp_path))

    assert _stored(config) == expected
    assert again["rn_dot"].config.hub_token == "tok-dot"
    out = capsys.readouterr().out
    assert "runner r-dot (rn_dot): its hub token is sealed in the host store" in out
    assert "is no longer read" in out and "`blizzard runner token set --runner r-dot`" in out
    assert "tok-dot" not in out


def test_a_renamed_runner_keeps_presenting_its_sealed_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _upgraded_host(tmp_path, monkeypatch)
    _seeded(config)
    HostLayout(tmp_path).config_path.write_text(_host_text(config.db_url, dot_name="r-renamed"))
    (tmp_path / ".env").write_text("")

    renamed = load_host_config(tmp_path)
    built = _seeded(renamed)

    assert built["rn_dot"].config.name == "r-renamed"
    graph = build_host_process(renamed, list(built.values()))
    try:
        runner = next(runner for runner in graph.runners if runner.config.runner_id == "rn_dot")
        assert runner.hub_token.auth_headers() == {"Authorization": "Bearer tok-dot"}
    finally:
        graph.close()


@pytest.mark.parametrize("lost", ["directory-deleted", "variable-swapped"])
def test_a_lost_key_leaves_each_runner_built_and_refusing_with_the_token_set_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], lost: str
) -> None:
    config = _upgraded_host(tmp_path, monkeypatch)
    if lost == "variable-swapped":
        monkeypatch.setenv(ENV_HOST_SECRET_KEY, "A" * 43 + "=")
    _seeded(config)
    if lost == "directory-deleted":
        shutil.rmtree(tmp_path / "data" / "secret-keys")
    else:
        monkeypatch.setenv(ENV_HOST_SECRET_KEY, "B" * 43 + "=")
    capsys.readouterr()

    built = _seeded(load_host_config(tmp_path))

    assert {runner.token.state for runner in built.values()} == {StoredTokenState.UNREADABLE}
    assert {runner.config.hub_token for runner in built.values()} == {""}
    out = capsys.readouterr().out
    for name, _ in _RUNNERS.values():
        assert f"`blizzard runner token set --runner {name}`" in out
    graph = build_host_process(config, list(built.values()))
    try:
        runner = graph.runners[0]
        assert runner.hub_token.auth_headers() == {}
        stepper = RunnerStepper(runner.config, process_graph=runner)
        try:
            ctx = stepper._context()  # pyright: ignore[reportPrivateUsage]
            # The hub URL answers nothing: had the check asked it, the verdict would still be unchecked.
            IdentityCheck(ctx).run()
        finally:
            stepper.close()
        verdict = runner.identity_verdict.verdict()
        assert verdict.state is IdentityState.REFUSED
        assert verdict.reset_command is not None and "blizzard runner token set --runner r-env" in verdict.reset_command
        assert tick_scope(verdict, registration_checks=False) is TickScope.SKIP
    finally:
        graph.close()
