"""The shared supported-models predicate both daemons apply."""

from __future__ import annotations

import pytest

from blizzard.foundation.model_support import model_supported


@pytest.mark.unit
@pytest.mark.parametrize(
    ("entry", "supported", "expected"),
    [
        ("opus", ("opus",), True),
        ("Opus", ("opus",), False),
        ("claude-opus-5", ("claude-*",), True),
        ("openai/gpt-5", ("openai/*",), True),
        ("openai/gpt-5/mini", ("*",), True),
        ("anthropic/x", ("openai/*",), False),
        ("opus", (), False),
        ("blizzard:frontier", ("*",), False),
        ("blizzard:frontier", ("blizzard:frontier",), False),
    ],
)
def test_a_native_entry_matches_an_exact_name_or_glob_and_a_tier_never_does(
    entry: str, supported: tuple[str, ...], expected: bool
) -> None:
    assert model_supported(entry, supported) is expected
