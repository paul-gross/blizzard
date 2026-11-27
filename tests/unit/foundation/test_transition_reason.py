from blizzard.foundation.transition_reason import normalize_transition_reason


def test_normalizes_and_caps_unicode_codepoints() -> None:
    assert normalize_transition_reason(None) is None
    assert normalize_transition_reason(" \n ") is None
    assert normalize_transition_reason("  hello \n") == "hello"
    assert normalize_transition_reason("  " + "🍦" * 2001 + "  ") == "🍦" * 2000
    assert normalize_transition_reason("a" * 1999 + " " + "b") == "a" * 1999 + " "
