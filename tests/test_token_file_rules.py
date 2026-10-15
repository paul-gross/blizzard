"""The ``.env`` rules a runner token's moves stand on: a removed assignment takes every repeat of it and nothing else,
and the driver's removal leaves a file holding no assignment as it was."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.hub.token_file import assigned_token, without_token

pytestmark = pytest.mark.unit


def test_a_removed_token_takes_every_assignment_of_it_and_keeps_every_other_line() -> None:
    text = "# kept\nBZ_HUB_TOKEN_R2=one\nBZ_HOST_TOKEN=host\n BZ_HUB_TOKEN_R2 = 'two'\nBZ_HUB_TOKEN_R3=three\n"

    removed = without_token(text, "BZ_HUB_TOKEN_R2")

    assert removed == "# kept\nBZ_HOST_TOKEN=host\nBZ_HUB_TOKEN_R3=three\n"
    assert assigned_token(removed, "BZ_HUB_TOKEN_R2") == ""
    assert without_token("BZ_HUB_TOKEN_R2=one\n", "BZ_HUB_TOKEN_R2") == ""


def test_the_drivers_removal_rewrites_only_a_file_holding_the_assignment(tmp_path: Path) -> None:
    token = HubTokenFile.of(tmp_path, "BZ_HUB_TOKEN_R2")
    token.remove()
    assert not token.path.exists()
    token.path.write_text("BZ_HOST_TOKEN=host\n")
    before = token.path.stat().st_mtime_ns
    token.remove()
    assert token.path.stat().st_mtime_ns == before
    token.write("one")
    token.remove()
    assert token.path.read_text() == "BZ_HOST_TOKEN=host\n"
