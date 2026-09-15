"""Every collected test carries one of the six declared verification-tier markers.

An unmarked test still runs in the default suite (nothing in collection skips it) but is
invisible to both ``-m unit`` and ``-m component``, so their totals silently under-count
the default run instead of the gap failing a run outright.
"""

from __future__ import annotations

import pytest

from tests.conftest import UNMARKED_TEST_NODEIDS

pytestmark = pytest.mark.unit


def test_every_collected_test_carries_a_tier_marker() -> None:
    assert UNMARKED_TEST_NODEIDS == [], (
        f"these collected tests carry none of unit/component/service/e2e/crash_sweep/journey: {UNMARKED_TEST_NODEIDS}"
    )
