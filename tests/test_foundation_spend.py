"""``blizzard.foundation.spend.SpendFigure`` (unit tier): billed plus estimated, ``~`` when an estimate
contributed, ``+`` when some row carried neither amount."""

from __future__ import annotations

import pytest

from blizzard.foundation.spend import SpendFigure

pytestmark = pytest.mark.unit


def test_a_billed_only_figure_renders_bare() -> None:
    figure = SpendFigure(4.0, None, partial=False)
    assert figure.amount() == pytest.approx(4.0)
    assert figure.rendered() == "$4.00"


def test_an_estimate_only_figure_is_its_estimate_marked() -> None:
    figure = SpendFigure(0.0, 5.0, partial=False)
    assert figure.amount() == pytest.approx(5.0)
    assert figure.rendered() == "~$5.00"


def test_an_estimate_of_zero_still_marks() -> None:
    assert SpendFigure(4.0, 0.0, partial=False).rendered() == "~$4.00"


def test_a_partial_figure_suffixes_a_plus() -> None:
    assert SpendFigure(4.0, None, partial=True).rendered() == "$4.00+"


def test_both_markers_sum_both_columns() -> None:
    figure = SpendFigure(10.0, 51.37, partial=True)
    assert figure.amount() == pytest.approx(61.37)
    assert figure.rendered() == "~$61.37+"


def test_the_rendering_rounds_to_the_cent() -> None:
    assert SpendFigure(0.004, 0.003, partial=False).rendered() == "~$0.01"
