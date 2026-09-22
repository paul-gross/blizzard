"""``harness_shared.normalize_opencode_version`` (blizzard#438) — the one version normalizer
both the live OpenCode probe and the health/capability-snapshot path route a membership or
corpus-lookup check through, moved here verbatim from its original OpenCode-probe-only home
so the two paths can never disagree about what "the observed version" means. Also
``harness_shared.version_admitted``, the one semver-range membership check both paths route
through in turn."""

from __future__ import annotations

import pytest
from packaging.specifiers import SpecifierSet

from blizzard.runner.harness.internal.harness_shared import normalize_opencode_version, version_admitted

pytestmark = pytest.mark.unit


def test_none_normalizes_to_none() -> None:
    assert normalize_opencode_version(None) is None


def test_a_bare_version_normalizes_to_itself() -> None:
    assert normalize_opencode_version("1.18.25\n") == "1.18.25"


def test_an_opencode_prefixed_version_normalizes_to_the_bare_version() -> None:
    assert normalize_opencode_version("opencode 1.18.25\n") == "1.18.25"


def test_an_opencode_version_prefixed_version_normalizes_to_the_bare_version() -> None:
    assert normalize_opencode_version("opencode version 1.18.25\n") == "1.18.25"


def test_a_v_prefixed_version_normalizes_to_the_bare_version() -> None:
    assert normalize_opencode_version("v1.18.25\n") == "1.18.25"


def test_a_prerelease_suffix_is_retained() -> None:
    assert normalize_opencode_version("1.18.25-beta.1\n") == "1.18.25-beta.1"


def test_more_than_one_non_blank_line_is_unparseable() -> None:
    assert normalize_opencode_version("1.18.25\nsecond line\n") is None


def test_no_version_shape_at_all_is_unparseable() -> None:
    assert normalize_opencode_version("not a version\n") is None


def test_empty_output_is_unparseable() -> None:
    assert normalize_opencode_version("") is None


_RANGE = SpecifierSet(">=1.18.25,<2.0")


def test_a_version_above_the_lower_bound_is_admitted() -> None:
    assert version_admitted("1.18.31", _RANGE) is True


def test_the_lower_bound_itself_is_admitted() -> None:
    assert version_admitted("1.18.25", _RANGE) is True


def test_a_version_below_the_lower_bound_is_not_admitted() -> None:
    assert version_admitted("1.18.24", _RANGE) is False


def test_the_upper_bound_is_not_admitted() -> None:
    assert version_admitted("2.0.0", _RANGE) is False


def test_a_prerelease_inside_the_bounds_is_not_admitted() -> None:
    """Pre-releases stay excluded even though `1.19.0rc1` would otherwise fall inside the
    range — explicit, never `SpecifierSet`'s own single-candidate default, which would
    otherwise admit a lone pre-release checked on its own (its own ``filter`` heuristic)."""
    assert version_admitted("1.19.0rc1", _RANGE) is False


@pytest.mark.parametrize("version", ["1.19.0-1", "1.19.0-beta.1", "1.19.0-0.3.7"])
def test_a_semver_prerelease_suffix_is_not_admitted(version: str) -> None:
    """A numeric-only semver pre-release (`1.19.0-1`) is a post-release to PEP 440, so it is
    rejected on its semver shape before `Version` ever parses it."""
    assert version_admitted(version, _RANGE) is False


def test_an_unparsable_version_is_not_admitted_rather_than_raising() -> None:
    assert version_admitted("not-a-version", _RANGE) is False
    assert version_admitted("1.18.25 extra", _RANGE) is False
