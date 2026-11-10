"""``blizzard.hub.cli.views.Cost`` (unit tier): one amount, ``~`` when estimated, ``+`` when partial."""

from __future__ import annotations

from blizzard.hub.cli.views import Cost, pointer_label


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


def test_a_pointer_reads_as_its_hub_rendered_label_over_everything_else() -> None:
    row: dict[str, object] = {"label": "Fix the thing", "slug": "gh", "source": "github", "ref": "12"}

    assert pointer_label(row) == "Fix the thing"


def test_a_pointer_without_a_label_reads_as_slug_hash_ref() -> None:
    assert pointer_label({"slug": "gh", "source": "github", "ref": "12"}) == "gh#12"


def test_a_pointer_with_an_empty_label_falls_through_to_the_slug() -> None:
    assert pointer_label({"label": "", "slug": "gh", "source": "github", "ref": "12"}) == "gh#12"


def test_a_pointer_without_a_slug_reads_as_source_hash_ref() -> None:
    assert pointer_label({"source": "github", "ref": "12"}) == "github#12"


def test_a_pointer_with_a_null_slug_falls_through_to_the_source() -> None:
    assert pointer_label({"label": None, "slug": None, "source": "github", "ref": "12"}) == "github#12"
