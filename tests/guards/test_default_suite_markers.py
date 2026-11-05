"""Every collected test carries one of the declared verification-tier markers, or collection is refused.

An unmarked test still runs in the default suite (nothing in collection skips it) but is
invisible to both ``-m unit`` and ``-m component``, so their totals silently under-count
the default run instead of the gap failing a run outright.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from tests.support.suite_plugin import refuse_unmarked


class _Item:
    """The slice of a collected item the marker check reads."""

    def __init__(self, nodeid: str, *markers: str) -> None:
        self.nodeid = nodeid
        self._markers = markers

    def iter_markers(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=name) for name in self._markers]


def _refuse(*items: _Item) -> None:
    refuse_unmarked(cast("list[pytest.Item]", list(items)))


def test_a_run_whose_every_test_carries_a_tier_marker_passes() -> None:
    _refuse(_Item("a.py::test_a", "unit"), _Item("b.py::test_b", "parametrize", "service"))


def test_a_test_carrying_no_tier_marker_refuses_the_run_and_is_named() -> None:
    with pytest.raises(pytest.UsageError, match=r"carry none of .*: b\.py::test_b$"):
        _refuse(_Item("a.py::test_a", "component"), _Item("b.py::test_b", "parametrize"))
