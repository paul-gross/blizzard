"""``claude_code_health.normalize_claude_code_version`` (blizzard#606) — the version
normalizer Claude Code's own ``IHarnessHealthProbe.normalize_version`` routes a membership
check through, cased the same way as ``harness_shared.normalize_opencode_version``'s own
tests, beside which this lives. Also ``version_admitted`` against Claude Code's admitted
range, pinning the tested-assumption cases the plan records."""

from __future__ import annotations

import pytest

from blizzard.runner.harness.internal.claude_code_health import (
    ADMITTED_CLAUDE_CODE_RANGE,
    normalize_claude_code_version,
)
from blizzard.runner.harness.internal.harness_shared import version_admitted

pytestmark = pytest.mark.unit


def test_none_normalizes_to_none() -> None:
    assert normalize_claude_code_version(None) is None


def test_a_bare_version_normalizes_to_itself() -> None:
    assert normalize_claude_code_version("2.1.278\n") == "2.1.278"


def test_the_real_claude_code_shape_normalizes_to_the_bare_version() -> None:
    assert normalize_claude_code_version("2.1.278 (Claude Code)\n") == "2.1.278"


def test_a_v_prefixed_version_normalizes_to_the_bare_version() -> None:
    assert normalize_claude_code_version("v2.1.278\n") == "2.1.278"


def test_a_prerelease_suffix_is_retained() -> None:
    assert normalize_claude_code_version("2.2.0-beta.1 (Claude Code)\n") == "2.2.0-beta.1"


def test_more_than_one_non_blank_line_is_unparseable() -> None:
    assert normalize_claude_code_version("2.1.278 (Claude Code)\nsecond line\n") is None


def test_no_version_shape_at_all_is_unparseable() -> None:
    assert normalize_claude_code_version("unrecognized arguments: --version\n") is None


def test_empty_output_is_unparseable() -> None:
    assert normalize_claude_code_version("") is None


def test_2_1_278_is_admitted() -> None:
    assert version_admitted("2.1.278", ADMITTED_CLAUDE_CODE_RANGE) is True


def test_2_1_0_the_lower_bound_is_admitted() -> None:
    assert version_admitted("2.1.0", ADMITTED_CLAUDE_CODE_RANGE) is True


def test_2_1_220_is_admitted() -> None:
    assert version_admitted("2.1.220", ADMITTED_CLAUDE_CODE_RANGE) is True


def test_2_9_9_is_admitted() -> None:
    assert version_admitted("2.9.9", ADMITTED_CLAUDE_CODE_RANGE) is True


def test_2_0_99_below_the_lower_bound_is_not_admitted() -> None:
    assert version_admitted("2.0.99", ADMITTED_CLAUDE_CODE_RANGE) is False


def test_3_0_0_the_upper_bound_is_not_admitted() -> None:
    assert version_admitted("3.0.0", ADMITTED_CLAUDE_CODE_RANGE) is False


def test_a_prerelease_is_not_admitted() -> None:
    assert version_admitted("2.2.0-beta.1", ADMITTED_CLAUDE_CODE_RANGE) is False


def test_2_1_300rc1_is_not_admitted() -> None:
    assert version_admitted("2.1.300rc1", ADMITTED_CLAUDE_CODE_RANGE) is False
