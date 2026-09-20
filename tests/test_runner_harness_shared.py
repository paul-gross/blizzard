"""``harness_shared.normalize_harness_version`` (blizzard#438) — the one version normalizer
both the live OpenCode probe and the health/capability-snapshot path route a membership or
corpus-lookup check through, moved here verbatim from its original OpenCode-probe-only home
so the two paths can never disagree about what "the observed version" means."""

from __future__ import annotations

import pytest

from blizzard.runner.harness.internal.harness_shared import normalize_harness_version

pytestmark = pytest.mark.unit


def test_none_normalizes_to_none() -> None:
    assert normalize_harness_version(None) is None


def test_a_bare_version_normalizes_to_itself() -> None:
    assert normalize_harness_version("1.18.25\n") == "1.18.25"


def test_an_opencode_prefixed_version_normalizes_to_the_bare_version() -> None:
    assert normalize_harness_version("opencode 1.18.25\n") == "1.18.25"


def test_an_opencode_version_prefixed_version_normalizes_to_the_bare_version() -> None:
    assert normalize_harness_version("opencode version 1.18.25\n") == "1.18.25"


def test_a_v_prefixed_version_normalizes_to_the_bare_version() -> None:
    assert normalize_harness_version("v1.18.25\n") == "1.18.25"


def test_a_prerelease_suffix_is_retained() -> None:
    assert normalize_harness_version("1.18.25-beta.1\n") == "1.18.25-beta.1"


def test_more_than_one_non_blank_line_is_unparseable() -> None:
    assert normalize_harness_version("1.18.25\nsecond line\n") is None


def test_no_version_shape_at_all_is_unparseable() -> None:
    assert normalize_harness_version("not a version\n") is None


def test_empty_output_is_unparseable() -> None:
    assert normalize_harness_version("") is None
