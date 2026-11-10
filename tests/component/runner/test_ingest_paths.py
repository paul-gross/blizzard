"""The ingest edge resolves home and symlinks before the pure path decision."""

import tomllib
from pathlib import Path

import pytest

from blizzard.runner.host_layout import HostLayout
from blizzard.runner.ingest import RunnerIngest
from tests.support.config_inputs import load_host_config
from tests.support.runner_ingest import MOVING, two_hosts
from tests.support.runner_init_fakes import FakeInitHub


def test_path_preparation_expands_home_and_resolves_symlink_containment(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    config = load_host_config(hosts.there)
    entry = config.sole_runner(MOVING)
    home = hosts.there / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    (home / "marker").write_text("marker")
    outside = tmp_path / "outside"
    outside.mkdir()
    (hosts.there / "escape").symlink_to(outside, target_is_directory=True)
    text = HostLayout(hosts.there).config_path.read_text()
    before, after = text.split(f'name = "{MOVING}"', 1)
    after = after.replace('runner_prompt_file = ""', 'runner_prompt_file = "~/marker"', 1)
    after = after.replace('transcripts_root = ""', 'transcripts_root = "escape/missing"', 1)
    text = before + f'name = "{MOVING}"' + after
    ingest = object.__new__(RunnerIngest)
    object.__setattr__(ingest, "host", load_host_config(hosts.here))
    table, files = ingest._runner_plan(config, entry, {}, text=text)  # pyright: ignore[reportPrivateUsage]
    parsed = tomllib.loads(table)["runner"][0]
    assert parsed["runner_prompt_file"] == str(hosts.here / "runners" / MOVING / "home" / "marker")
    assert parsed["transcripts_root"] == "escape/missing"
    assert (home / "marker", hosts.here / "runners" / MOVING / "home" / "marker") in files
    assert all(not str(located).startswith(str(outside)) for located, _ in files)


@pytest.mark.parametrize("literal", ['""', "42"])
def test_empty_or_non_string_path_values_are_not_rewritten(
    tmp_path: Path, fake_init_hub: FakeInitHub, literal: str
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    config = load_host_config(hosts.there)
    entry = config.sole_runner(MOVING)
    text = HostLayout(hosts.there).config_path.read_text()
    before, after = text.split(f'name = "{MOVING}"', 1)
    after = after.replace('runner_prompt_file = ""', f"runner_prompt_file = {literal}", 1)
    ingest = object.__new__(RunnerIngest)
    object.__setattr__(ingest, "host", load_host_config(hosts.here))
    table, files = ingest._runner_plan(  # pyright: ignore[reportPrivateUsage]
        config, entry, {}, text=before + f'name = "{MOVING}"' + after
    )
    assert tomllib.loads(table)["runner"][0]["runner_prompt_file"] == tomllib.loads(f"value = {literal}")["value"]
    assert all(located not in (hosts.there, hosts.there / "42") for located, _ in files)
