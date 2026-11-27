from blizzard.foundation.transition_reason import normalize_transition_reason


def test_normalizes_and_caps_unicode_codepoints() -> None:
    assert normalize_transition_reason(None) is None
    assert normalize_transition_reason(" \n ") is None
    assert normalize_transition_reason("  hello \n") == "hello"
    assert normalize_transition_reason("  " + "🍦" * 2001 + "  ") == "🍦" * 2000
    assert normalize_transition_reason("a" * 1999 + " " + "b") == "a" * 1999 + " "


def test_clipped_prefix_preserves_boundary_only_with_provenance() -> None:
    reason = "a" * 1999 + " "
    assert normalize_transition_reason(reason) == "a" * 1999
    assert normalize_transition_reason(reason, truncated=True) == reason
    assert normalize_transition_reason("  short  ", truncated=True) == "short"
