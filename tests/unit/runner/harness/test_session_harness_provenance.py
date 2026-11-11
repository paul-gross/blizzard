"""Runner session-owner migration and composite store behavior."""

from __future__ import annotations

from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.identity import SessionReference


def test_session_reference_is_a_composite_key() -> None:
    """A same raw id remains distinguishable in every domain value that carries it."""
    claude = SessionReference(CLAUDE_CODE_HARNESS_ID, "shared")
    other = SessionReference("other_harness", "shared")
    assert claude != other
    assert {claude, other} == {claude, other}
