"""Which artifact writes count as landing markers — the writes that owe close intents (unit tier)."""

from __future__ import annotations

from blizzard.hub.domain.chunk.no_code import no_code_marker
from blizzard.hub.store.internal.chunk_rows import is_landing_marker

_DECLARATION = {"repo": "widget", "record": "rec", "base_branch": "master", "branch": "feat", "commit": "abc"}


def test_a_merged_marker_naming_a_revision_is_a_landing_marker() -> None:
    assert is_landing_marker("merged/widget", "sha")


def test_a_no_code_marker_is_not_a_landing_marker() -> None:
    name, data = no_code_marker(_DECLARATION)

    assert name.startswith("no-code/")
    assert not is_landing_marker(name, data)
