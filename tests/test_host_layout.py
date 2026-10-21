"""A host directory's layout, its lock, and the targeted writes to ``blizzard-host.toml``."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from blizzard.foundation.runner_token_names import HUB_TOKEN_VARIABLE_PREFIX, hub_token_variable
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_file import (
    append_runner,
    append_subscription,
    append_workspace,
    remove_runner,
    repoint_workspace_prompt,
    runner_table,
    with_runner_values,
    write_runner_id,
    write_scaffold,
)
from blizzard.runner.host_layout import Graduation, HostHeld, HostLayout, HostLock, hold_host
from tests.config_inputs import STRICT

pytestmark = pytest.mark.unit

_FILE = """# kept comment
hub_url = "https://hub.example"

[[workspace]]
id = "ws"
root = "/w"
prompt_package = "winter"

[[runner]]
name = "r-one"
projects = []

[runner.worker]
env_passthrough = []

[[runner]]
# second
name = "r-two"
id = "rn_old"
projects = []
"""


@pytest.mark.parametrize(
    ("name", "variable"),
    [
        ("r-claude", "BZ_HUB_TOKEN_R_CLAUDE"),
        ("r-chatgpt", "BZ_HUB_TOKEN_R_CHATGPT"),
        ("Mixed.Case 9", "BZ_HUB_TOKEN_MIXED_CASE_9"),
        ("a--b", "BZ_HUB_TOKEN_A__B"),
    ],
)
def test_a_runner_name_normalises_to_its_token_variable(name: str, variable: str) -> None:
    assert hub_token_variable(name) == variable
    assert variable.startswith(HUB_TOKEN_VARIABLE_PREFIX)


def test_the_layout_names_its_files_and_its_graduation_state(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    assert layout.graduation() is Graduation.UNINITIALISED
    assert layout.store_path == tmp_path / "data" / "host.db"
    assert layout.lock_path == tmp_path / "data" / "host.lock"
    assert layout.socket_path == tmp_path / "host.sock"
    assert layout.legacy_store_path == tmp_path / "data" / "runner.db"
    assert layout.legacy_socket_path == tmp_path / "runner.sock"
    assert layout.default_db_url == f"sqlite:///{layout.store_path.resolve()}"
    layout.legacy_config_path.write_text("")
    assert layout.graduation() is Graduation.UNGRADUATED
    layout.config_path.write_text("")
    assert layout.graduation() is Graduation.GRADUATED


def test_the_lock_is_exclusive_and_a_refusal_names_the_holders_pid(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    with HostLock.acquire(layout):
        assert layout.lock_path.read_text() == str(os.getpid())
        with pytest.raises(HostHeld, match=str(os.getpid())) as refused:
            HostLock.acquire(layout)
        assert refused.value.pid == os.getpid()
    HostLock.acquire(layout).release()


def test_a_lock_another_process_holds_is_refused_and_freed_when_it_ends(tmp_path: Path) -> None:
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; from blizzard.runner.host_layout import HostLayout, HostLock;"
            "HostLock.acquire(HostLayout(Path(sys.argv[1]))); print('ready', flush=True); sys.stdin.read()",
            str(tmp_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,  # drained-pipe: this spawn reads its own stdout
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "ready"
        with pytest.raises(HostHeld) as refused:
            HostLock.acquire(HostLayout(tmp_path))
        assert refused.value.pid == holder.pid
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait(10)
    HostLock.acquire(HostLayout(tmp_path)).release()


def test_a_scaffold_is_written_whole_and_never_over_an_existing_file(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path / "new")
    write_scaffold(layout, "port = 1\n")
    assert layout.config_path.read_text() == "port = 1\n"
    assert layout.config_path.stat().st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        write_scaffold(layout, "port = 2\n")
    assert layout.config_path.read_text() == "port = 1\n"
    assert [p.name for p in layout.root.iterdir()] == ["blizzard-host.toml"]


def test_a_runner_id_lands_in_the_named_runner_and_a_rerun_changes_nothing(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    layout.config_path.write_text(_FILE)
    write_runner_id(layout, "r-one", "rn_new", strict=STRICT)
    document = tomllib.loads(layout.config_path.read_text())
    assert [r.get("id") for r in document["runner"]] == ["rn_new", "rn_old"]
    assert layout.config_path.read_text().startswith("# kept comment\n")
    assert "# second" in layout.config_path.read_text()
    once = layout.config_path.read_text()
    write_runner_id(layout, "r-one", "rn_new", strict=STRICT)
    assert layout.config_path.read_text() == once
    write_runner_id(layout, "r-two", "rn_other", strict=STRICT)
    assert tomllib.loads(layout.config_path.read_text())["runner"][1]["id"] == "rn_other"


def test_a_runner_id_for_an_unnamed_runner_is_refused(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    layout.config_path.write_text(_FILE)
    with pytest.raises(ConfigError, match="r-none"):
        write_runner_id(layout, "r-none", "rn_x", strict=STRICT)
    assert layout.config_path.read_text() == _FILE


def test_a_runner_id_lands_in_a_file_without_a_trailing_newline(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    layout.config_path.write_text('[[runner]]\nname = "r"')
    write_runner_id(layout, "r", "rn_1", strict=STRICT)
    assert tomllib.loads(layout.config_path.read_text())["runner"][0] == {"name": "r", "id": "rn_1"}


def test_a_prompt_install_repoints_the_workspace_prompt_keys_only(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    layout.config_path.write_text(_FILE)
    repoint_workspace_prompt(layout, "ws", file_path="/tmp/workspace-prompt.md", strict=STRICT)
    document = tomllib.loads(layout.config_path.read_text())
    assert document["workspace"][0] == {
        "id": "ws",
        "root": "/w",
        "prompt_package": "",
        "prompt_file": "/tmp/workspace-prompt.md",
    }
    assert document["runner"][1]["id"] == "rn_old"
    assert layout.config_path.read_text().startswith("# kept comment\n")


_MULTILINE = '''hub_url = "https://hub.example"

[[workspace]]
id = "ws"
root = "/w"
prompt = """
Intro
[not a header]
[[workspace]]
id = "inside"
"""
tags = [
    "a",
    "[[runner]]",
]

[[runner]]
name = "r-one"
runner_prompt = \'\'\'
[also not a header]
\'\'\'
projects = [
    "p",
]
'''


def test_a_runner_id_lands_in_the_runner_beside_a_bracket_led_line_in_a_multi_line_string(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    layout.config_path.write_text(_MULTILINE)
    write_runner_id(layout, "r-one", "rn_new", strict=STRICT)
    after = tomllib.loads(layout.config_path.read_text())
    assert after["runner"][0]["id"] == "rn_new"
    assert after["runner"][0]["runner_prompt"] == "[also not a header]\n"
    assert after["workspace"] == tomllib.loads(_MULTILINE)["workspace"]
    assert after["runner"][0]["projects"] == ["p"]


def test_a_prompt_install_repoints_a_workspace_whose_prompt_holds_a_bracket_led_line(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    layout.config_path.write_text(_MULTILINE)
    repoint_workspace_prompt(layout, "ws", file_path="/tmp/p.md", strict=STRICT)
    after = tomllib.loads(layout.config_path.read_text())
    before = tomllib.loads(_MULTILINE)
    assert after["workspace"][0] == {**before["workspace"][0], "prompt_file": "/tmp/p.md", "prompt_package": ""}
    assert after["runner"] == before["runner"]


def test_a_replaced_multi_line_value_is_replaced_whole(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    layout.config_path.write_text('[[workspace]]\nid = "ws"\nprompt_file = [\n  "x",\n  "[y]",\n]\nroot = "/w"\n')
    repoint_workspace_prompt(layout, "ws", file_path="/tmp/p.md", strict=STRICT)
    assert tomllib.loads(layout.config_path.read_text())["workspace"][0] == {
        "id": "ws",
        "root": "/w",
        "prompt_file": "/tmp/p.md",
        "prompt_package": "",
    }


def test_a_file_that_is_not_toml_is_refused_and_left_as_it_was(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    layout.config_path.write_text('[[runner]]\nname = "r"\nbroken = \n')
    with pytest.raises(ConfigError):
        write_runner_id(layout, "r", "rn_1", strict=STRICT)
    assert layout.config_path.read_text() == '[[runner]]\nname = "r"\nbroken = \n'


_THIRD = """
[[runner]]
id = "rn_three"
name = "r-three"
projects = [{ project = "p", workspace = "ws", agents = 1 }]

[runner.harness]
autonomy = "full"
"""


def test_an_appended_runner_lands_whole_after_everything_and_keeps_every_comment(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    layout.config_path.write_text(_FILE)
    append_runner(layout, _THIRD, strict=STRICT)
    text = layout.config_path.read_text()
    assert text == _FILE + _THIRD
    runners = tomllib.loads(text)["runner"]
    assert [r["name"] for r in runners] == ["r-one", "r-two", "r-three"]
    assert runners[2]["harness"] == {"autonomy": "full"}
    assert runners[0]["worker"] == {"env_passthrough": []}


def test_a_runner_appended_to_a_file_without_a_trailing_newline_is_set_on_its_own_line(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    layout.config_path.write_text(_FILE.rstrip("\n"))
    append_runner(layout, _THIRD, strict=STRICT)
    assert [r["name"] for r in tomllib.loads(layout.config_path.read_text())["runner"]] == ["r-one", "r-two", "r-three"]


def test_an_append_that_is_not_one_runner_table_is_refused_leaving_the_file(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    layout.config_path.write_text(_FILE)
    with pytest.raises(ConfigError):
        append_runner(layout, '\n[[workspace]]\nid = "other"\n', strict=STRICT)
    with pytest.raises(ConfigError):
        append_runner(layout, '\n[runner]\nname = "x"\n', strict=STRICT)
    assert layout.config_path.read_text() == _FILE


def test_a_removed_runner_goes_whole_with_its_sub_tables_and_the_others_stay_as_they_were(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    layout.config_path.write_text(_FILE + _THIRD)
    remove_runner(layout, "r-one", strict=STRICT)
    text = layout.config_path.read_text()
    runners = tomllib.loads(text)["runner"]
    assert [r["name"] for r in runners] == ["r-two", "r-three"]
    assert all("worker" not in r for r in runners)
    assert text.startswith("# kept comment\n") and "# second" in text
    remove_runner(layout, "r-three", strict=STRICT)
    assert layout.config_path.read_text() == _FILE.replace(
        '[[runner]]\nname = "r-one"\nprojects = []\n\n[runner.worker]\nenv_passthrough = []\n\n', ""
    )


def test_removing_the_last_runner_leaves_the_host_tables(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    layout.config_path.write_text(_FILE)
    remove_runner(layout, "r-two", strict=STRICT)
    remove_runner(layout, "r-one", strict=STRICT)
    document = tomllib.loads(layout.config_path.read_text())
    assert "runner" not in document
    assert document["workspace"][0]["id"] == "ws"


def test_removing_a_runner_the_file_does_not_name_is_refused(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    layout.config_path.write_text(_FILE)
    with pytest.raises(ConfigError, match="r-none"):
        remove_runner(layout, "r-none", strict=STRICT)
    assert layout.config_path.read_text() == _FILE


def test_an_appended_workspace_and_subscription_land_whole_after_everything(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    layout.config_path.write_text(_FILE)
    workspace = '\n[[workspace]]\nid = "ws-two"\nroot = "/w2"\n\n[[workspace.repo]]\nname = "a"\nurl = "u"\n'
    subscription = '\n[[subscription]]\nslug = "s"\nname = "S"\nprovider = "anthropic"\n'
    append_workspace(layout, workspace, strict=STRICT)
    append_subscription(layout, subscription, strict=STRICT)
    parsed = tomllib.loads(layout.config_path.read_text())
    assert layout.config_path.read_text() == _FILE + workspace + subscription
    assert [w["id"] for w in parsed["workspace"]] == ["ws", "ws-two"]
    assert parsed["workspace"][1]["repo"] == [{"name": "a", "url": "u"}]
    assert [s["slug"] for s in parsed["subscription"]] == ["s"]


def test_an_append_of_another_kind_of_table_is_refused_leaving_the_file(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    layout.config_path.write_text(_FILE)
    with pytest.raises(ConfigError):
        append_workspace(layout, '\n[[runner]]\nname = "x"\n', strict=STRICT)
    with pytest.raises(ConfigError):
        append_subscription(layout, '\n[[subscription]]\nslug = "a"\n\n[[subscription]]\nslug = "b"\n', strict=STRICT)
    assert layout.config_path.read_text() == _FILE


def test_a_runners_table_is_read_whole_and_appends_to_another_file_as_it_was(tmp_path: Path) -> None:
    source, destination = HostLayout(tmp_path / "a"), HostLayout(tmp_path / "b")
    for layout in (source, destination):
        layout.root.mkdir()
    source.config_path.write_text(_FILE)
    destination.config_path.write_text('hub_url = "https://hub.example"\n')
    append_runner(destination, runner_table(source, "r-one", strict=STRICT), strict=STRICT)
    [runner] = tomllib.loads(destination.config_path.read_text())["runner"]
    assert runner == tomllib.loads(_FILE)["runner"][0]


def test_runner_values_replace_the_keys_they_name_in_the_runner_and_its_sub_tables() -> None:
    table = (
        '\n[[runner]]\nname = "r"\nprojects = []\nworker_settings_path = "/old/w.json"\n'
        '\n[runner.harness]\nconfig_dir = "/old/bundle"\n\n[runner.opencode]\nworker_config_path = "/old/o.json"\n'
    )
    edited = with_runner_values(
        table,
        {
            ("worker_settings_path",): '"/new/w.json"',
            ("harness", "config_dir"): '"/new/bundle"',
            ("opencode", "worker_config_path"): '"/new/o.json"',
            ("projects",): '[{ project = "p", workspace = "w", agents = 1 }]',
        },
        strict=STRICT,
    )
    [runner] = tomllib.loads(edited)["runner"]
    assert runner["worker_settings_path"] == "/new/w.json"
    assert runner["harness"] == {"config_dir": "/new/bundle"}
    assert runner["opencode"] == {"worker_config_path": "/new/o.json"}
    assert runner["projects"] == [{"project": "p", "workspace": "w", "agents": 1}]
    with pytest.raises(ConfigError, match="sets none of"):
        with_runner_values(table, {("opencode", "auth_path"): '"/x"'}, strict=STRICT)


def test_a_held_lock_refuses_the_hold_naming_the_pid(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    with (
        HostLock.acquire(layout),
        pytest.raises(HostHeld, match=f"pid {os.getpid()}"),
        hold_host(layout, (), lambda _: None),
    ):
        pass


def test_the_hold_refuses_each_named_socket_that_answers_and_probes_no_other(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    probed: list[Path] = []

    def probe(sock: Path) -> str | None:
        probed.append(sock)
        return f"a daemon answers at {sock}" if sock == layout.legacy_socket_path else None

    with hold_host(layout, (layout.socket_path,), probe):
        pass
    assert probed == [layout.socket_path]
    with (
        pytest.raises(HostHeld, match=str(layout.legacy_socket_path)),
        hold_host(layout, (layout.socket_path, layout.legacy_socket_path), probe),
    ):
        pass


def test_the_hold_releases_the_lock_after_a_refusal_and_a_normal_exit(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    with hold_host(layout, (), lambda _: None):
        pass
    with pytest.raises(HostHeld), hold_host(layout, (layout.socket_path,), lambda sock: f"answering at {sock}"):
        pass
    HostLock.acquire(layout).release()
