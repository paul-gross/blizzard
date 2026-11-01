"""``blizzard runner init`` over a host directory (component tier): the host it registers — kept by a held host token
the hub knows, added otherwise, and added again with every runner when the hub forgot it — and each runner it joins
under that host, written into the host file by its hub id, its token under the variable its name derives, and its
identity row into the store — each write completed by running init again after a crash between them."""

from __future__ import annotations

import sqlite3
import tomllib
from contextlib import closing
from pathlib import Path

import pytest
from click.testing import Result

from blizzard.cli.main import blizzard
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.runner_token_names import HOST_TOKEN_VARIABLE
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.composition import build_host_stores
from blizzard.runner.config import DEFAULT_WORKSPACE_ID, ProjectBinding
from blizzard.runner.host_config import runner_toml, scaffold_runner
from blizzard.runner.host_file import append_runner
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.client import TokenIdentity
from blizzard.runner.hub.token_file import ENV_FILENAME
from blizzard.runner.runtime import sqlite_store_file
from tests.config_inputs import STRICT, load_host_config
from tests.host_files import load_runner_config
from tests.runner_fakes import runner_store_errors
from tests.runner_init_fakes import FakeInitHub, FakeTokenIdentityReader, init_runner
from tests.session_store_fakes import InMemorySessionStore

pytestmark = pytest.mark.component

_HUB = "http://hub.test:8421"


def _init(hub: FakeInitHub, root: Path, *args: str) -> Result:
    return init_runner(hub).invoke(blizzard, ["runner", "init", str(root), "--hub", _HUB, *args])


def _identity_rows(root: Path) -> list[tuple[str, str]]:
    with closing(sqlite3.connect(sqlite_store_file(load_host_config(root).db_url))) as conn:
        return [(row[0], row[1]) for row in conn.execute("SELECT runner_id, runner_name FROM runner_identity")]


def _configured_id(root: Path) -> str | None:
    runner, *_ = tomllib.loads(HostLayout(root).config_path.read_text())["runner"]
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
        sqlite_store_file(load_host_config(root).db_url).read_bytes(),
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
    assert "restore the token of rn_init1" in refused.output
    assert "dropping every fact below the hub's high-water mark" in refused.output
    assert _files(root) == before
    assert _identity_rows(root) == [("rn_init1", "runner-local")]


def test_init_over_a_store_naming_its_runner_and_a_foreign_one_refuses_advising_the_right_store_and_no_reset(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "host"
    assert _init(fake_init_hub, root).exit_code == 0
    with closing(sqlite3.connect(sqlite_store_file(load_host_config(root).db_url))) as conn, conn:
        conn.execute(
            "INSERT INTO runner_identity (runner_id, runner_name, registered_at) "
            "SELECT 'rn_ghost', runner_name, registered_at FROM runner_identity"
        )
    before = _files(root)

    refused = _init(fake_init_hub, root)

    assert refused.exit_code != 0
    assert "rn_ghost is configured by no [[runner]]" in refused.output
    assert "stop and restore the right store" in refused.output
    assert "store reset" not in refused.output
    assert _files(root) == before


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


# --- registering the host ------------------------------------------------------------------------


def _env(root: Path) -> str:
    return (root / ENV_FILENAME).read_text()


def _with_a_second_runner(root: Path, name: str = "r-two") -> None:
    """Append a ``[[runner]]`` with no id to ``root``'s host file, as an operator adding one by hand would."""
    append_runner(
        HostLayout(root),
        runner_toml(scaffold_runner(root, name, bindings=(ProjectBinding("default", DEFAULT_WORKSPACE_ID, 1),))),
        strict=STRICT,
    )


def test_init_on_an_open_hub_registers_a_host_named_after_its_first_runner_and_adds_the_runner_under_it(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "host"

    result = _init(fake_init_hub, root)

    assert result.exit_code == 0, result.output
    assert [(h.host_id, h.host_name) for h in fake_init_hub.admin.hosts] == [("hs_init1", "runner-local")]
    assert fake_init_hub.admin.held == {"hs_init1": ["rn_init1"]}
    assert fake_init_hub.calls[0].operator_token is None
    assert f"{HOST_TOKEN_VARIABLE}=init-host-token-1\n" in _env(root)
    assert "registered host runner-local (hs_init1)" in result.output
    assert load_host_config(root).host_token.value == "init-host-token-1"


def test_init_registers_the_host_under_the_operator_session_held_for_its_hub(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    sessions = InMemorySessionStore({_HUB: "operator-session"})

    result = init_runner(fake_init_hub, sessions=sessions).invoke(
        blizzard, ["runner", "init", str(tmp_path / "host"), "--hub", _HUB, "--tenant", "ten_bravo"]
    )

    assert result.exit_code == 0, result.output
    assert fake_init_hub.calls[0].operator_token == "operator-session"
    assert fake_init_hub.admin.host_tenants == {"hs_init1": "ten_bravo"}
    assert fake_init_hub.admin.held == {"hs_init1": ["rn_init1"]}


def test_init_keeps_the_host_a_process_environment_host_token_names_and_writes_none(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    held = fake_init_hub.admin.add_host("enrolled")
    monkeypatch.setenv(HOST_TOKEN_VARIABLE, held.token)
    root = tmp_path / "host"

    result = _init(fake_init_hub, root)

    assert result.exit_code == 0, result.output
    assert len(fake_init_hub.admin.hosts) == 1
    assert fake_init_hub.admin.held == {held.host_id: ["rn_init1"]}
    assert HOST_TOKEN_VARIABLE not in _env(root)
    assert f"host enrolled ({held.host_id})" in result.output and "keeps its token" in result.output


def test_init_keeps_the_host_the_dotenv_host_token_names(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    held = fake_init_hub.admin.add_host("enrolled")
    root = tmp_path / "host"
    root.mkdir()
    (root / ENV_FILENAME).write_text(f"{HOST_TOKEN_VARIABLE}={held.token}\n")

    result = _init(fake_init_hub, root)

    assert result.exit_code == 0, result.output
    assert len(fake_init_hub.admin.hosts) == 1
    assert fake_init_hub.admin.held == {held.host_id: ["rn_init1"]}


def test_init_joins_every_runner_of_a_host_of_several_under_the_one_host(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "host"
    assert _init(fake_init_hub, root).exit_code == 0
    _with_a_second_runner(root)

    result = _init(fake_init_hub, root)

    assert result.exit_code == 0, result.output
    assert fake_init_hub.admin.held == {"hs_init1": ["rn_init1", "rn_init2"]}
    assert [r["id"] for r in tomllib.loads(HostLayout(root).config_path.read_text())["runner"]] == [
        "rn_init1",
        "rn_init2",
    ]
    assert sorted(_identity_rows(root)) == [("rn_init1", "runner-local"), ("rn_init2", "r-two")]


def test_init_whose_host_the_hub_forgot_stops_without_allow_readd_and_re_adds_host_and_every_runner_with_it(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "host"
    assert _init(fake_init_hub, root).exit_code == 0
    _with_a_second_runner(root)
    assert _init(fake_init_hub, root).exit_code == 0
    fake_init_hub.admin.hosts.clear()  # the hub's data is reset: neither the host's token nor a runner's is known
    fake_init_hub.admin.added.clear()
    before = _files(root)

    refused = _init(fake_init_hub, root)

    assert refused.exit_code != 0
    assert "does not know this host's token" in refused.output and "--allow-readd" in refused.output
    assert _files(root) == before

    readded = _init(fake_init_hub, root, "--allow-readd")

    assert readded.exit_code == 0, readded.output
    assert [h.host_id for h in fake_init_hub.admin.hosts] == ["hs_init2"]
    assert fake_init_hub.admin.adopted[-1] == ()
    assert fake_init_hub.admin.held["hs_init2"] == ["rn_init1", "rn_init2"]
    assert f"{HOST_TOKEN_VARIABLE}=init-host-token-2\n" in _env(root)
    store = sqlite_store_file(load_host_config(root).db_url)
    assert len([p for p in store.parent.iterdir() if p.name.startswith("host.db.") and p.suffix != ".lock"]) == 1
    assert sorted(_identity_rows(root)) == [("rn_init1", "runner-local"), ("rn_init2", "r-two")]


def test_an_unknown_host_token_from_the_process_environment_stops_init_even_with_allow_readd(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "host"
    assert _init(fake_init_hub, root).exit_code == 0
    monkeypatch.setenv(HOST_TOKEN_VARIABLE, "stale-from-env")
    before = _files(root)

    refused = _init(fake_init_hub, root, "--allow-readd")

    assert refused.exit_code != 0
    assert f"${HOST_TOKEN_VARIABLE}" in refused.output and "would go unread" in refused.output
    assert _files(root) == before
    assert len(fake_init_hub.admin.hosts) == 1


def test_init_whose_host_the_hub_retired_stops_even_with_allow_readd(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "host"
    assert _init(fake_init_hub, root).exit_code == 0
    fake_init_hub.admin.retired_hosts.add("hs_init1")
    before = _files(root)

    refused = _init(fake_init_hub, root, "--allow-readd")

    assert refused.exit_code != 0
    assert "refuses this host's token as a retired host's" in refused.output
    assert _files(root) == before
    assert len(fake_init_hub.admin.hosts) == 1


def test_init_whose_host_serves_another_tenant_than_named_stops(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = tmp_path / "host"
    assert _init(fake_init_hub, root, "--tenant", "ten_alpha").exit_code == 0

    refused = _init(fake_init_hub, root, "--tenant", "ten_bravo")

    assert refused.exit_code != 0
    assert "host hs_init1, which serves tenant ten_alpha" in refused.output
    assert len(fake_init_hub.admin.hosts) == 1


def test_init_whose_kept_runner_stands_under_no_host_of_its_own_stops_naming_the_adoption(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "host"
    assert _init(fake_init_hub, root).exit_code == 0
    fake_init_hub.admin.held["hs_init1"].clear()  # released at the hub

    refused = _init(fake_init_hub, root)

    assert refused.exit_code != 0
    assert f"`blizzard hub runner-host adopt hs_init1 rn_init1 --hub-url {_HUB}`" in refused.output


def test_a_new_host_adopts_the_runner_whose_held_token_the_hub_knows(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "host"
    assert _init(fake_init_hub, root).exit_code == 0
    env = root / ENV_FILENAME
    env.write_text(
        "".join(line for line in env.read_text().splitlines(keepends=True) if HOST_TOKEN_VARIABLE not in line)
    )

    result = _init(fake_init_hub, root)

    assert result.exit_code == 0, result.output
    assert fake_init_hub.admin.adopted == [(), ("rn_init1",)]
    assert fake_init_hub.admin.held["hs_init2"] == ["rn_init1"]
    assert len(fake_init_hub.admin.added) == 1
