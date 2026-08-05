"""Pinning tests for comment-defended runner-harness decisions (issue #270).

Unit tier, alongside ``tests/test_runner_harness_adapter.py``'s coverage of the same
adapter: each test here pins a decision whose only defence was prose.
"""

from __future__ import annotations

import pytest

from blizzard.runner.harness.internal import claude_code_adapter as adapter_module
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter


@pytest.mark.unit
def test_an_unmapped_tier_alias_never_substitutes_downward(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tier aliases are unordered roles, not an ordered scale (issue #144): an unmapped
    entry is unresolvable, not substituted with the next tier down."""
    monkeypatch.setattr(adapter_module, "_BUILTIN_TIERS", {"blizzard:advanced": "opus", "blizzard:basic": "sonnet"})
    adapter = ClaudeCodeAdapter(binary="claude", model="claude-opus-5")

    assert adapter.resolve_model(["blizzard:frontier"]) == "claude-opus-5"
    # …and an author who wants a fallback writes one into the list.
    assert adapter.resolve_model(["blizzard:frontier", "blizzard:basic"]) == "sonnet"
