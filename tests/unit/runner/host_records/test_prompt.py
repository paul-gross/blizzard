"""A workspace record's prompt edit: stating one prompt setting returns the other two to their defaults."""

from __future__ import annotations

import pytest

from blizzard.runner.host_records.model import HostRecord, RecordKind
from blizzard.runner.host_records.prompt import PromptChange


def _workspace(**settings: object) -> HostRecord:
    return HostRecord(RecordKind.WORKSPACE, "ws", 1, settings)


def test_stating_one_prompt_setting_returns_the_other_two_to_their_defaults() -> None:
    change = PromptChange.stating("prompt_file", "p.md")

    assert change.body() == {"prompt": None, "prompt_package": None, "prompt_file": "p.md"}
    held = _workspace(prompt="OLD", prompt_package="sample", root="/r")
    write = held.edit(change.sets, change.unsets, if_match=1)
    assert write is not None and dict(write.record.settings) == {"root": "/r", "prompt_file": "p.md"}
    assert change.applied_to(held.settings) == {"root": "/r", "prompt_file": "p.md"}


def test_clearing_the_prompt_returns_all_three_to_their_defaults() -> None:
    change = PromptChange.cleared()

    assert change.applied_to({"prompt": "x", "prompt_file": "f", "root": "/r"}) == {"root": "/r"}
    assert change.unsets == frozenset({"prompt", "prompt_file", "prompt_package"})


def test_only_a_prompt_setting_is_stated_as_one() -> None:
    with pytest.raises(ValueError, match="root"):
        PromptChange.stating("root", "/r")
