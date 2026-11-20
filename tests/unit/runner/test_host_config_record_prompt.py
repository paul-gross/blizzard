"""A workspace record's prompt: its prompt settings resolved as the host file's ``[[workspace]]`` resolves them."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_config import record_prompt
from blizzard.runner.host_records.model import HostRecord, RecordKind
from blizzard.runner.host_records.prompt import EffectiveWorkspacePrompt


def _workspace(**settings: object) -> HostRecord:
    return HostRecord(RecordKind.WORKSPACE, "ws", 1, settings)


def test_an_inline_prompt_is_the_records_prompt(tmp_path: Path) -> None:
    assert record_prompt(_workspace(prompt="RECORD"), tmp_path) == EffectiveWorkspacePrompt(
        "RECORD", "config", "inline prompt"
    )


def test_a_relative_prompt_file_resolves_against_the_host_root(tmp_path: Path) -> None:
    (tmp_path / "p.md").write_text("FROM FILE")

    assert record_prompt(_workspace(prompt_file="p.md"), tmp_path) == EffectiveWorkspacePrompt(
        "FROM FILE", "config", "file p.md"
    )


def test_a_record_stating_no_prompt_answers_the_default(tmp_path: Path) -> None:
    assert record_prompt(_workspace(root="/ws"), tmp_path) == EffectiveWorkspacePrompt("", "default", "none")


def test_a_missing_prompt_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="prompt_file does not exist"):
        record_prompt(_workspace(prompt_file="gone.md"), tmp_path)
