"""The ingest edge resolves home and symlinks before the pure path decision."""

import tomllib
from pathlib import Path

import pytest

from blizzard.runner.composition import build_runner_ingest
from blizzard.runner.host_config import HostConfig
from blizzard.runner.host_layout import HostLayout
from tests.support.config_inputs import load_host_config
from tests.support.runner_ingest import MOVING, two_hosts
from tests.support.runner_init_fakes import FakeInitHub


def _plan(here: HostConfig, there: Path) -> tuple[str, tuple[tuple[Path, Path], ...]]:
    ingest = build_runner_ingest(here, FakeInitHub(), holder=lambda _path: None)  # pyright: ignore[reportArgumentType]
    source = ingest.sources.open_host(there)
    entry = ingest.sources.runner(source, MOVING)
    return ingest._runner_plan(source, entry, {})  # pyright: ignore[reportPrivateUsage]


def test_path_preparation_expands_home_and_resolves_symlink_containment(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    here = load_host_config(hosts.here)
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
    HostLayout(hosts.there).config_path.write_text(before + f'name = "{MOVING}"' + after)
    table, files = _plan(here, hosts.there)
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
    here = load_host_config(hosts.here)
    text = HostLayout(hosts.there).config_path.read_text()
    before, after = text.split(f'name = "{MOVING}"', 1)
    after = after.replace('runner_prompt_file = ""', f"runner_prompt_file = {literal}", 1)
    HostLayout(hosts.there).config_path.write_text(before + f'name = "{MOVING}"' + after)
    table, files = _plan(here, hosts.there)
    assert tomllib.loads(table)["runner"][0]["runner_prompt_file"] == tomllib.loads(f"value = {literal}")["value"]
    assert all(located not in (hosts.there, hosts.there / "42") for located, _ in files)
