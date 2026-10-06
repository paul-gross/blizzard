"""``blizzard runner init`` against a real hub process on ``auth.mode = "none"`` — the operator's
local-to-local bootstrap, and what a runner's hub-minted id survives: a rename, a deleted store, a
retirement, and a reset of its hub's data (service tier). Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import contextlib
import dataclasses
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy.engine import make_url

from blizzard.runner.config import RunnerConfig
from blizzard.runner.hub.identity import RunnerIdentity
from tests.e2e.test_acceptance_loop import _await_http, _free_port, _terminate
from tests.runner_fakes import make_store
from tests.runner_join import (
    RUNNER_BIN,
    held_token,
    join_runner,
    run_runner_init,
    runner_spawn_env,
    token_identity,
)
from tests.service.support import poll_until, service_gate
from tests.support import daemon_log_sink, read_daemon_log

pytestmark = [pytest.mark.service, service_gate]

_HUB_BIN = Path(sys.executable).parent / "blizzard-hub"


@contextlib.contextmanager
def _hub(hub_dir: Path, port: int) -> Iterator[httpx.Client]:
    """A real ``blizzard hub host`` over ``hub_dir`` on its default ``auth.mode = "none"``, which
    ``hub init`` creates first when the directory holds no hub yet."""
    if not hub_dir.exists():
        subprocess.run([str(_HUB_BIN), "init", str(hub_dir)], check=True, capture_output=True, text=True)
    log = hub_dir / "daemon.log"
    proc = subprocess.Popen(
        [str(_HUB_BIN), "host", "--dir", str(hub_dir), "--host", "127.0.0.1", "--port", str(port)],
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30.0)
    try:
        _await_http(proc, client, "/api/health", log=log)
        yield client
    finally:
        client.close()
        _terminate(proc)


def _identity(config: RunnerConfig) -> RunnerIdentity | None:
    """The runner store's identity row — written only by a registration the hub accepted."""
    return make_store(config.db_url).runner_identity()


@contextlib.contextmanager
def _runner(runner_dir: Path, hub_url: str) -> Iterator[RunnerConfig]:
    """``blizzard runner host`` over a joined ``runner_dir``, yielded once its store holds the identity
    row; the external-usage sampler reads a credentials path that is never created."""
    config = dataclasses.replace(
        RunnerConfig.load(runner_dir),
        external_usage_credentials_path=str(runner_dir / "no-such-credentials.json"),
    )
    config.config_path.write_text(config.to_toml())
    log = runner_dir / "daemon.log"
    proc = subprocess.Popen(
        [str(RUNNER_BIN), "host", "--dir", str(runner_dir), "--host", "127.0.0.1", "--port", str(_free_port())],
        env=runner_spawn_env(hub_url, {"BZ_RUNNER_TICK_SECONDS": "0.5"}),
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        assert poll_until(lambda: _identity(config) is not None, timeout=30.0), read_daemon_log(log)
        yield config
    finally:
        _terminate(proc)


def _runner_ids(hub: httpx.Client) -> list[str]:
    return [runner["runner_id"] for runner in hub.get("/api/runners").json()["runners"]]


def _view(hub: httpx.Client, runner_id: str) -> dict:
    view = hub.get(f"/api/runners/{runner_id}")
    assert view.status_code == 200, view.text
    return view.json()


def test_init_adds_a_runner_at_a_hub_without_sign_in_and_its_daemon_connects_under_the_minted_id(
    tmp_path: Path,
) -> None:
    port = _free_port()
    hub_url = f"http://127.0.0.1:{port}"
    runner_dir = tmp_path / "runner"
    with _hub(tmp_path / "hub", port) as hub:
        joined = join_runner(runner_dir, hub_url)
        name = RunnerConfig.load(runner_dir).name
        assert joined.runner_id.startswith("rn_"), joined
        assert joined.runner_name == name
        assert _view(hub, joined.runner_id)["connection"] == "never_connected"

        with _runner(runner_dir, hub_url) as config:
            identity = _identity(config)
            assert identity is not None and (identity.runner_id, identity.runner_name) == (joined.runner_id, name)
            assert poll_until(lambda: _view(hub, joined.runner_id)["connection"] == "online")

        # Re-running init on a joined directory keeps its token and adds nothing.
        again = run_runner_init(runner_dir, hub_url)
        assert again.returncode == 0, again.stderr
        assert held_token(runner_dir) == joined.token
        assert _runner_ids(hub) == [joined.runner_id]


def test_a_runner_renamed_in_its_config_shows_the_new_name_under_the_same_id_after_a_restart(
    tmp_path: Path,
) -> None:
    port = _free_port()
    hub_url = f"http://127.0.0.1:{port}"
    runner_dir = tmp_path / "runner"
    with _hub(tmp_path / "hub", port) as hub:
        joined = join_runner(runner_dir, hub_url)
        with _runner(runner_dir, hub_url):
            pass

        config = RunnerConfig.load(runner_dir)
        config.config_path.write_text(dataclasses.replace(config, name="renamed-runner").to_toml())
        with _runner(runner_dir, hub_url) as renamed:
            assert poll_until(lambda: _view(hub, joined.runner_id)["runner_name"] == "renamed-runner")
            assert poll_until(lambda: getattr(_identity(renamed), "runner_name", None) == "renamed-runner")
            identity = _identity(renamed)
            assert identity is not None and identity.runner_id == joined.runner_id
        assert _runner_ids(hub) == [joined.runner_id]


def test_a_runner_whose_store_is_deleted_re_registers_under_the_same_id(tmp_path: Path) -> None:
    port = _free_port()
    hub_url = f"http://127.0.0.1:{port}"
    runner_dir = tmp_path / "runner"
    with _hub(tmp_path / "hub", port) as hub:
        joined = join_runner(runner_dir, hub_url)
        with _runner(runner_dir, hub_url) as config:
            database = Path(str(make_url(config.db_url).database))

        for path in database.parent.glob(f"{database.name}*"):
            path.unlink()
        # Init rebuilds the store and keeps the token, which the hub still knows.
        assert join_runner(runner_dir, hub_url) == joined
        with _runner(runner_dir, hub_url) as rebuilt:
            identity = _identity(rebuilt)
            assert identity is not None and identity.runner_id == joined.runner_id
        assert _runner_ids(hub) == [joined.runner_id]


def test_init_for_a_retired_runner_names_reinstate_and_leaves_the_token_untouched(tmp_path: Path) -> None:
    port = _free_port()
    hub_url = f"http://127.0.0.1:{port}"
    runner_dir = tmp_path / "runner"
    with _hub(tmp_path / "hub", port) as hub:
        joined = join_runner(runner_dir, hub_url)
        held = (runner_dir / ".env").read_bytes()
        retired = hub.post(f"/api/runners/{joined.runner_id}/retire", json={"by": "operator", "force": False})
        assert retired.status_code == 200, retired.text

        refused = run_runner_init(runner_dir, hub_url)
        assert refused.returncode != 0, refused.stdout
        assert f"blizzard hub runner reinstate {joined.runner_id}" in refused.stdout + refused.stderr
        assert (runner_dir / ".env").read_bytes() == held
        # The list leaves a retired runner out, so any runner it shows is one init added.
        assert _runner_ids(hub) == []
        assert _view(hub, joined.runner_id)["retired"] is True


def test_after_its_hub_s_data_is_reset_init_re_adds_the_runner_only_with_allow_readd(tmp_path: Path) -> None:
    port = _free_port()
    hub_url = f"http://127.0.0.1:{port}"
    runner_dir = tmp_path / "runner"
    with _hub(tmp_path / "hub", port):
        first = join_runner(runner_dir, hub_url)
    held = (runner_dir / ".env").read_bytes()

    # The same hub URL over fresh data: the token the runner holds is unknown there.
    with _hub(tmp_path / "hub-reset", port) as hub:
        refused = run_runner_init(runner_dir, hub_url)
        assert refused.returncode != 0, refused.stdout
        message = refused.stdout + refused.stderr
        assert hub_url in message and "--allow-readd" in message, message
        assert (runner_dir / ".env").read_bytes() == held
        assert _runner_ids(hub) == []

        readded = join_runner(runner_dir, hub_url, allow_readd=True)
        assert readded.runner_id.startswith("rn_") and readded.runner_id != first.runner_id
        assert readded.token != first.token
        assert token_identity(hub_url, first.token).status_code == 401
        assert _runner_ids(hub) == [readded.runner_id]
