"""``blizzard.hub.cli.views.Cost`` (unit tier): one amount, ``~`` when estimated, ``+`` when partial."""

from __future__ import annotations

import pytest

from blizzard.hub.cli.views import Cost

pytestmark = pytest.mark.unit


def test_neither_marker_renders_a_bare_figure() -> None:
    assert Cost.of({"cost_usd": 4.00}).rendered == "$4.00"


def test_an_estimate_prefixes_a_tilde_and_folds_into_the_one_figure() -> None:
    assert Cost.of({"cost_usd": 4.00, "estimated_cost_usd": 0.05}).rendered == "~$4.05"


def test_a_partial_total_suffixes_a_plus() -> None:
    assert Cost.of({"cost_usd": 4.00, "cost_partial": True}).rendered == "$4.00+"


def test_an_estimated_partial_total_carries_both_markers() -> None:
    assert Cost.of({"cost_usd": 4.00, "estimated_cost_usd": 0.05, "cost_partial": True}).rendered == "~$4.05+"


def test_an_entirely_estimated_total_still_reads_as_estimated() -> None:
    assert Cost.of({"cost_usd": 0.0, "estimated_cost_usd": 0.07}).rendered == "~$0.07"


def test_cost_of_none_renders_a_bare_zero() -> None:
    assert Cost.of(None).rendered == "$0.00"


def test_an_estimate_of_exactly_zero_still_prefixes_a_tilde() -> None:
    assert Cost.of({"cost_usd": 4.00, "estimated_cost_usd": 0.0}).rendered == "~$4.00"
