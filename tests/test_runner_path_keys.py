"""The declared list of ``[[runner]]`` path keys ``runner ingest`` rewrites names every runner field that holds a path,
and each key reaches the field it declares."""

from __future__ import annotations

import dataclasses
import typing
from pathlib import Path

import pytest

from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.claude_code.section import ClaudeCodeSection
from blizzard.runner.harness.opencode.section import OpenCodeSection
from blizzard.runner.host_config import RUNNER_PATH_KEYS, RunnerPathKey
from tests.config_inputs import load_host_config
from tests.host_files import write_minimal_host

pytestmark = pytest.mark.unit

#: The classes a ``[[runner]]`` is parsed onto, by the name a path key's field is declared under.
_OWNERS: dict[str, type] = {
    "RunnerConfig": RunnerConfig,
    "ClaudeCodeSection": ClaudeCodeSection,
    "OpenCodeSection": OpenCodeSection,
}

#: Fields that hold a path but are no ``[[runner]]`` key: the host directory and the store, both the host's own.
_NOT_RUNNER_KEYS = frozenset({"RunnerConfig.root", "RunnerConfig.db_url"})


def _holds_a_path(owner: type, field: dataclasses.Field[object]) -> bool:
    """A field typed ``Path``, or a string field named as a path is: ``*_path``, ``*_file``, ``*_root``, ``*_dir``."""
    hint = typing.get_type_hints(owner)[field.name]
    if Path in (hint, *typing.get_args(hint)):
        return True
    return field.name.endswith(("_path", "_file", "_root", "_dir"))


def test_the_path_keys_name_every_runner_field_that_holds_a_path() -> None:
    holding = {
        f"{name}.{field.name}"
        for name, owner in _OWNERS.items()
        for field in dataclasses.fields(owner)
        if _holds_a_path(owner, field)
    }
    assert {key.field for key in RUNNER_PATH_KEYS} == holding - _NOT_RUNNER_KEYS


@pytest.mark.parametrize("path_key", RUNNER_PATH_KEYS, ids=[".".join(k.key) for k in RUNNER_PATH_KEYS])
def test_each_path_key_reaches_the_field_it_declares(tmp_path: Path, path_key: RunnerPathKey) -> None:
    marker = str(tmp_path / "marker")
    *tables, key = path_key.key
    line = f'{key} = "{marker}"\n'
    write_minimal_host(tmp_path, runner=f"\n[runner.{'.'.join(tables)}]\n{line}" if tables else line)
    entry = load_host_config(tmp_path).sole_runner()
    assert entry.own is not None, entry.errors
    owner, field = path_key.field.split(".")
    parsed: object = {
        "RunnerConfig": entry.own,
        "ClaudeCodeSection": entry.own.harness_sections.of("claude_code"),
        "OpenCodeSection": entry.own.harness_sections.of("opencode"),
    }[owner]
    assert str(getattr(parsed, field)) == marker
