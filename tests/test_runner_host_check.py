"""``blizzard runner host --check`` (component tier): every check a start would make, each error named, and nothing
started — no socket, no lock, no store write."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from blizzard.cli.main import blizzard
from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.runner.host_layout import HostLayout, migrate_remedy
from blizzard.runner.hub.client import HubClientError, TokenIdentity, TokenRefusal
from blizzard.runner.runtime import Runtime, migration_runner, sqlite_store_file
from tests.host_files import load_runner_config
from tests.runner_init_fakes import FakeInitHub, FakeTokenIdentityReader, InitRunner, init_runner

pytestmark = pytest.mark.component

_HUB = "http://hub.test:8421"


def _joined(tmp_path: Path, hub: FakeInitHub) -> Path:
    root = tmp_path / "host"
    result = init_runner(hub).invoke(blizzard, ["runner", "init", str(root), "--hub", _HUB])
    assert result.exit_code == 0, result.output
    return root


def _check(hub: FakeInitHub, root: Path, *args: str) -> Result:
    cli: InitRunner = init_runner(hub)
    return cli.invoke(blizzard, ["runner", "host", "--dir", str(root), "--check", *args])


def _nothing_started(root: Path) -> None:
    layout = HostLayout(root)
    assert not layout.socket_path.exists()


def test_a_valid_host_checks_clean_resolving_each_token_to_its_id(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = _joined(tmp_path, fake_init_hub)
    layout = HostLayout(root)
    lock_before = layout.lock_path.stat().st_mtime_ns if layout.lock_path.exists() else None

    result = _check(fake_init_hub, root)

    assert result.exit_code == 0, result.output
    assert "runner runner-local (rn_init1): its token (.env) resolves to its id" in result.output
    assert "host ok" in result.output
    _nothing_started(root)
    assert (layout.lock_path.stat().st_mtime_ns if layout.lock_path.exists() else None) == lock_before


def test_a_token_that_resolves_to_another_runner_is_named_with_both_ids(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _joined(tmp_path, fake_init_hub)
    fake_init_hub.identity = FakeTokenIdentityReader(TokenIdentity(runner_id="rn_other", runner_name="other"))

    result = _check(fake_init_hub, root)

    assert result.exit_code != 0
    assert "runner runner-local (rn_init1)" in result.output
    assert "resolves to runner rn_other, not the configured rn_init1" in result.output
    _nothing_started(root)


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        (HubClientError("GET /api/fleet/identity failed: refused"), "cannot resolve its token at the hub"),
        (TokenRefusal(reason=RunnerTokenRefusalReason.REVOKED, runner_id="rn_init1"), "refuses its token"),
        (TokenRefusal(reason=RunnerTokenRefusalReason.UNKNOWN), "refuses its token"),
    ],
)
def test_an_unreachable_hub_and_a_refused_token_are_errors_of_the_token_check(
    tmp_path: Path, fake_init_hub: FakeInitHub, answer: HubClientError | TokenRefusal, said: str
) -> None:
    root = _joined(tmp_path, fake_init_hub)
    fake_init_hub.identity = FakeTokenIdentityReader(answer)

    result = _check(fake_init_hub, root)

    assert result.exit_code != 0
    assert said in result.output and _HUB in result.output
    _nothing_started(root)


def test_a_store_not_at_the_expected_revision_is_an_error_naming_the_migrate_command(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _joined(tmp_path, fake_init_hub)
    migration_runner(Runtime(root).init()).downgrade("20261011_1000_runner_scope")

    result = _check(fake_init_hub, root)

    assert result.exit_code != 0
    assert "store:" in result.output and migrate_remedy(root) in " ".join(result.output.split())
    _nothing_started(root)


def test_every_runner_error_and_the_store_revision_are_reported_together(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _joined(tmp_path, fake_init_hub)
    layout = HostLayout(root)
    layout.config_path.write_text(
        layout.config_path.read_text().replace('subscription = "anthropic"', 'subscription = "nope"')
    )
    migration_runner(Runtime(root).init()).downgrade("20261011_1000_runner_scope")

    result = _check(fake_init_hub, root)

    assert result.exit_code != 0
    assert "store:" in result.output
    assert "runner runner-local" in result.output and "names subscription 'nope'" in result.output


def test_every_host_error_is_named_and_a_file_that_is_refused_goes_no_further(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _joined(tmp_path, fake_init_hub)
    layout = HostLayout(root)
    layout.config_path.write_text('max_agents = 3\ntoken_env = "T"\n' + layout.config_path.read_text())

    result = _check(fake_init_hub, root)

    assert result.exit_code != 0
    lines = [line for line in result.output.splitlines() if "host: " in line]
    assert len(lines) >= 2, result.output
    assert any("max_agents" in line for line in lines) and any("token_env" in line for line in lines)
    assert "host ok" not in result.output


def test_a_host_that_is_held_checks_clean_because_checking_takes_nothing(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    from blizzard.runner.host_layout import HostLock

    root = _joined(tmp_path, fake_init_hub)

    with HostLock.acquire(HostLayout(root)):
        result = _check(fake_init_hub, root)

    assert result.exit_code == 0, result.output


def test_host_check_is_the_one_cli_runner_probe_free_of_a_real_hub(tmp_path: Path) -> None:
    """With the refused local address the suite pins, a check that reached a real hub would fail on connection."""
    root = tmp_path / "host"
    assert init_runner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0

    result = CliRunner().invoke(blizzard, ["runner", "host", "--dir", str(root), "--check"])

    assert result.exit_code != 0
    assert "cannot resolve its token" in result.output


def _reconfigure(root: Path, runner_id: str) -> None:
    layout = HostLayout(root)
    layout.config_path.write_text(layout.config_path.read_text().replace('id = "rn_init1"', f'id = "{runner_id}"'))


def test_a_store_that_names_another_runner_is_reported_with_both_ids_and_the_remedy(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _joined(tmp_path, fake_init_hub)
    _reconfigure(root, "rn_other")
    fake_init_hub.identity = FakeTokenIdentityReader(TokenIdentity(runner_id="rn_other", runner_name="other"))

    result = _check(fake_init_hub, root)

    assert result.exit_code != 0
    assert "the store belongs to runner rn_init1, not the configured rn_other" in result.output
    assert "correct the `id` in the host file to rn_init1" in result.output
    assert f"blizzard runner store reset {root}" in result.output
    assert "high-water mark" in result.output
    _nothing_started(root)


def test_a_tick_over_a_store_that_names_another_runner_refuses_before_any_work(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _joined(tmp_path, fake_init_hub)
    _reconfigure(root, "rn_other")

    result = init_runner(fake_init_hub).invoke(blizzard, ["runner", "tick", "--dir", str(root)])

    assert result.exit_code != 0
    assert "the store belongs to runner rn_init1, not the configured rn_other" in result.output
    assert "correct the `id` in the host file to rn_init1" in result.output


def test_a_graduated_store_whose_identity_row_is_the_configured_id_checks_clean(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _joined(tmp_path, fake_init_hub)

    assert "the store belongs" not in _check(fake_init_hub, root).output


def test_a_store_naming_the_configured_runner_and_another_is_refused_by_check_and_tick(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _joined(tmp_path, fake_init_hub)
    with closing(sqlite3.connect(sqlite_store_file(load_runner_config(root).db_url))) as conn, conn:
        conn.execute(
            "INSERT INTO runner_identity (runner_id, runner_name, registered_at) "
            "SELECT 'rn_other', runner_name, registered_at FROM runner_identity"
        )

    checked = _check(fake_init_hub, root)
    ticked = init_runner(fake_init_hub).invoke(blizzard, ["runner", "tick", "--dir", str(root)])

    for result in (checked, ticked):
        assert result.exit_code != 0
        assert "rn_init1" in result.output and "rn_other" in result.output
        assert "stop and restore the right store" in result.output
        assert "store reset" not in result.output
    _nothing_started(root)
