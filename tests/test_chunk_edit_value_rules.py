"""Chunk-edit value rules (unit tier, by value): a supplied text or list entry is trimmed,
and one blank once trimmed refuses the edit."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.operations.edit import BlankEditValue, filled, filled_entries

pytestmark = pytest.mark.unit


def test_a_filled_value_is_trimmed() -> None:
    assert filled("  high ", "default_effort") == "high"


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_a_value_blank_once_trimmed_is_refused_naming_its_field(value: str) -> None:
    with pytest.raises(BlankEditValue, match=r"^to_graph must not be blank$") as caught:
        filled(value, "to_graph")
    assert caught.value.field_name == "to_graph"


def test_entries_are_each_trimmed_and_an_empty_list_is_the_clear() -> None:
    assert filled_entries([" opus ", "sonnet"], "default_model") == ["opus", "sonnet"]
    assert filled_entries([], "default_model") == []


def test_one_blank_entry_refuses_the_whole_list() -> None:
    with pytest.raises(BlankEditValue, match=r"^default_model entries must not be blank$") as caught:
        filled_entries(["opus", "  "], "default_model")
    assert caught.value.field_name == "default_model"
