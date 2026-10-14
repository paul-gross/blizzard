"""``blizzard runner init`` over a host directory (component tier): the runner it joins is written into the host file
by its hub id, its token under the variable its name derives, and its identity row into the store — each write
completed by running init again after a crash between them."""

from __future__ import annotations

import sqlite3
import tomllib
from contextlib import closing
from pathlib import Path

import pytest
from click.testing import Result

from blizzard.cli.main import blizzard
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.composition import build_host_stores
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.client import TokenIdentity
from blizzard.runner.runtime import sqlite_store_file
from tests.host_files import load_runner_config
from tests.runner_fakes import runner_store_errors
from tests.runner_init_fakes import FakeInitHub, FakeTokenIdentityReader, init_runner

pytestmark = pytest.mark.component

_HUB = "http://hub.test:8421"


def _init(hub: FakeInitHub, root: Path, *args: str) -> Result:
    return init_runner(hub).invoke(blizzard, ["runner", "init", str(root), "--hub", _HUB, *args])


def _identity_rows(root: Path) -> list[tuple[str, str]]:
    with closing(sqlite3.connect(sqlite_store_file(load_runner_config(root).db_url))) as conn:
        return [(row[0], row[1]) for row in conn.execute("SELECT runner_id, runner_name FROM runner_identity")]


def _configured_id(root: Path) -> str | None:
    (runner,) = tomllib.loads(HostLayout(root).config_path.read_text())["runner"]
    return runner.get("id")


def test_init_writes_the_joined_runners_id_into_the_host_file_and_its_identity_row_into_the_store(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "host"

    result = _init(fake_init_hub, root)

    assert result.exit_code == 0, result.output
    assert _configured_id(root) == "rn_init1"
    assert _identity_rows(root) == [("rn_init1", "runner-local")]
    assert load_runner_config(root).runner_id == "rn_init1"


def test_a_kept_runner_gets_its_id_and_identity_row_from_the_hubs_answer(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "host"
    assert _init(fake_init_hub, root).exit_code == 0
    layout = HostLayout(root)
    layout.config_path.write_text(layout.config_path.read_text().replace('id = "rn_init1"\n', ""))
    with closing(sqlite3.connect(sqlite_store_file(load_runner_config(root).db_url))) as conn, conn:
        conn.execute("DELETE FROM runner_identity")

    result = _init(fake_init_hub, root)  # the token in .env is known: the runner is kept, nothing is added

    assert result.exit_code == 0, result.output
    assert len(fake_init_hub.admin.added) == 1
    assert _configured_id(root) == "rn_init1"
    assert _identity_rows(root) == [("rn_init1", "runner-local")]


def test_running_init_again_changes_nothing(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = tmp_path / "host"
    assert _init(fake_init_hub, root).exit_code == 0
    layout = HostLayout(root)
    host_file = layout.config_path.read_bytes()
    token_file = (root / ".env").read_bytes()

    assert _init(fake_init_hub, root).exit_code == 0

    assert layout.config_path.read_bytes() == host_file
    assert (root / ".env").read_bytes() == token_file
    assert _identity_rows(root) == [("rn_init1", "runner-local")]


def test_the_which_runners_read_is_the_audit_over_the_real_store_not_a_runner_view(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "host"
    assert _init(fake_init_hub, root).exit_code == 0
    engine = create_engine_from_url(load_runner_config(root).db_url)
    try:
        stores = build_host_stores(engine, errors=runner_store_errors(), clock=SystemClock())
        assert stores.runner_audit.named_runners() == ("rn_init1",)
    finally:
        engine.dispose()


def test_init_with_the_host_file_cleared_and_no_token_stops_naming_the_runner_the_store_holds(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "host"
    assert _init(fake_init_hub, root).exit_code == 0
    store = sqlite_store_file(load_runner_config(root).db_url)
    HostLayout(root).config_path.unlink()
    (root / ".env").write_text("")
    before = store.read_bytes()

    refused = _init(fake_init_hub, root)

    assert refused.exit_code != 0
    assert "rn_init1" in refused.output and "--allow-readd" in refused.output
    assert store.read_bytes() == before
    assert len(fake_init_hub.admin.added) == 1

    readded = _init(fake_init_hub, root, "--allow-readd")

    assert readded.exit_code == 0, readded.output
    assert [p.name for p in store.parent.iterdir() if p.name.startswith("host.db.")]
    assert _identity_rows(root) == [("rn_init2", "runner-local")]
    assert _configured_id(root) == "rn_init2"


def test_init_whose_token_belongs_to_another_runner_than_the_configured_id_refuses_leaving_every_file_as_it_was(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "host"
    assert _init(fake_init_hub, root).exit_code == 0
    layout = HostLayout(root)
    layout.config_path.write_text(layout.config_path.read_text().replace('id = "rn_init1"', 'id = "rn_configured"'))
    store = sqlite_store_file(load_runner_config(root).db_url)
    before = (layout.config_path.read_bytes(), (root / ".env").read_bytes(), store.read_bytes())

    refused = _init(fake_init_hub, root)

    assert refused.exit_code != 0
    assert "rn_configured" in refused.output and "rn_init1" in refused.output
    assert (layout.config_path.read_bytes(), (root / ".env").read_bytes(), store.read_bytes()) == before
    assert len(fake_init_hub.admin.added) == 1


def test_a_readd_replaces_the_configured_id_with_the_added_runners(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = tmp_path / "host"
    assert _init(fake_init_hub, root).exit_code == 0
    layout = HostLayout(root)
    layout.config_path.write_text(layout.config_path.read_text().replace('id = "rn_init1"', 'id = "rn_configured"'))
    fake_init_hub.admin.added.clear()  # the hub's data is reset: the held token is now unknown

    readded = _init(fake_init_hub, root, "--allow-readd")

    assert readded.exit_code == 0, readded.output
    assert _configured_id(root) == "rn_init1"
    assert _identity_rows(root) == [("rn_init1", "runner-local")]


def test_init_with_a_configured_id_and_no_token_over_an_empty_store_refuses_adding_a_runner_in_its_place(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "host"
    assert _init(fake_init_hub, root).exit_code == 0
    layout = HostLayout(root)
    with closing(sqlite3.connect(sqlite_store_file(load_runner_config(root).db_url))) as conn, conn:
        conn.execute("DELETE FROM runner_identity")
    (root / ".env").write_text("")
    before = (layout.config_path.read_bytes(), (root / ".env").read_bytes())

    refused = _init(fake_init_hub, root)

    assert refused.exit_code != 0
    assert "rn_init1" in refused.output and "--allow-readd" in refused.output
    assert (layout.config_path.read_bytes(), (root / ".env").read_bytes()) == before
    assert len(fake_init_hub.admin.added) == 1


def _hold_the_token_of(hub: FakeInitHub, root: Path, runner_id: str) -> None:
    """Scripts the hub to resolve the token held in ``root``'s ``.env`` to ``runner_id``."""
    hub.identity = FakeTokenIdentityReader(TokenIdentity(runner_id=runner_id, runner_name="runner-local"))


def _files(root: Path) -> tuple[bytes, bytes, bytes]:
    return (
        HostLayout(root).config_path.read_bytes(),
        (root / ".env").read_bytes(),
        sqlite_store_file(load_runner_config(root).db_url).read_bytes(),
    )


def test_init_holding_another_runners_token_over_a_store_naming_one_refuses_when_the_host_file_has_no_id(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "host"
    assert _init(fake_init_hub, root).exit_code == 0
    layout = HostLayout(root)
    layout.config_path.write_text(layout.config_path.read_text().replace('id = "rn_init1"\n', ""))
    assert _configured_id(root) is None
    _hold_the_token_of(fake_init_hub, root, "rn_other")
    before = _files(root)

    refused = _init(fake_init_hub, root)

    assert refused.exit_code != 0
    assert "rn_init1" in refused.output and "rn_other" in refused.output
    assert f"`blizzard runner store reset {root}`" in refused.output
    assert _files(root) == before
    assert _identity_rows(root) == [("rn_init1", "runner-local")]


def test_init_holding_another_runners_token_with_a_readd_that_set_nothing_aside_refuses_leaving_every_file(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "host"
    assert _init(fake_init_hub, root).exit_code == 0
    _hold_the_token_of(fake_init_hub, root, "rn_other")
    before = _files(root)

    refused = _init(fake_init_hub, root, "--allow-readd")

    assert refused.exit_code != 0
    assert "rn_init1" in refused.output and "rn_other" in refused.output
    assert _files(root) == before
    assert _identity_rows(root) == [("rn_init1", "runner-local")]


def test_init_re_run_after_a_crash_between_the_token_and_the_id_completes_the_id_under_a_readd(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "host"
    assert _init(fake_init_hub, root).exit_code == 0
    layout = HostLayout(root)
    layout.config_path.write_text(layout.config_path.read_text().replace('id = "rn_init1"', 'id = "rn_stale"'))
    with closing(sqlite3.connect(sqlite_store_file(load_runner_config(root).db_url))) as conn, conn:
        conn.execute("DELETE FROM runner_identity")  # the store the re-add set aside, migrated fresh

    rerun = _init(fake_init_hub, root, "--allow-readd")

    assert rerun.exit_code == 0, rerun.output
    assert _configured_id(root) == "rn_init1"
    assert _identity_rows(root) == [("rn_init1", "runner-local")]
    assert len(fake_init_hub.admin.added) == 1


def test_init_with_a_stale_id_and_no_readd_refuses_rather_than_exit_zero_with_a_mismatch(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "host"
    assert _init(fake_init_hub, root).exit_code == 0
    _hold_the_token_of(fake_init_hub, root, "rn_other")
    layout = HostLayout(root)
    layout.config_path.write_text(layout.config_path.read_text().replace('id = "rn_init1"', 'id = "rn_stale"'))
    before = _files(root)

    refused = _init(fake_init_hub, root)

    assert refused.exit_code != 0
    assert _files(root) == before
