"""``compute_sweeps`` (unit tier) — the pure fold over a routine's unwindowed
``finding_sets`` rows into the last-swept table (D2, D3, D4) and the windowed
measurement series (D2, D5). No store — a plain list of ``SweepFact`` in, a
``GardenSweeps`` out."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.garden_sweeps import SweepFact, compute_sweeps

pytestmark = pytest.mark.unit

_SINCE = datetime(2026, 1, 1, tzinfo=UTC)
_UNTIL = datetime(2026, 1, 15, tzinfo=UTC)


def _fact(
    finding_set_id: str,
    *,
    scope_slug: str,
    day: int,
    measurement: str | None = None,
    revisions: dict[str, str] | None = None,
) -> SweepFact:
    return SweepFact(
        finding_set_id=finding_set_id,
        scope_slug=scope_slug,
        produced_at=datetime(2026, 1, day, tzinfo=UTC),
        revisions=revisions or {},
        measurement=measurement,
    )


def test_a_scope_with_no_set_reads_never() -> None:
    sweeps = compute_sweeps([], routine_name="nightly", scope_slugs=["blizzard"], since=_SINCE, until=_UNTIL)

    (row,) = sweeps.last_swept
    assert row.scope_slug == "blizzard"
    assert row.finding_set_id is None
    assert row.produced_at is None
    assert row.revisions == {}


def test_a_scope_with_several_sets_reports_the_newest_by_produced_at() -> None:
    facts = [
        _fact("fins_1", scope_slug="blizzard", day=2, revisions={"blizzard": "aaa"}),
        _fact("fins_2", scope_slug="blizzard", day=9, revisions={"blizzard": "bbb"}),
        _fact("fins_3", scope_slug="blizzard", day=5, revisions={"blizzard": "ccc"}),
    ]

    sweeps = compute_sweeps(facts, routine_name="nightly", scope_slugs=["blizzard"], since=_SINCE, until=_UNTIL)

    (row,) = sweeps.last_swept
    assert row.finding_set_id == "fins_2"
    assert row.produced_at == datetime(2026, 1, 9, tzinfo=UTC)
    assert row.revisions == {"blizzard": "bbb"}


def test_a_tie_on_produced_at_breaks_on_the_higher_finding_set_id() -> None:
    tied = datetime(2026, 1, 5, tzinfo=UTC)
    facts = [
        SweepFact(finding_set_id="fins_a", scope_slug="blizzard", produced_at=tied, revisions={}, measurement=None),
        SweepFact(finding_set_id="fins_b", scope_slug="blizzard", produced_at=tied, revisions={}, measurement=None),
    ]

    sweeps = compute_sweeps(facts, routine_name="nightly", scope_slugs=["blizzard"], since=_SINCE, until=_UNTIL)

    assert sweeps.last_swept[0].finding_set_id == "fins_b"


def test_a_measurement_outside_the_window_is_excluded_while_its_scopes_last_swept_is_not() -> None:
    facts = [_fact("fins_1", scope_slug="blizzard", day=20, measurement="score: 4")]

    sweeps = compute_sweeps(facts, routine_name="nightly", scope_slugs=["blizzard"], since=_SINCE, until=_UNTIL)

    assert sweeps.measurements == []
    assert sweeps.last_swept[0].finding_set_id == "fins_1"


def test_measurements_inside_the_window_are_reported_in_produced_at_order() -> None:
    facts = [
        _fact("fins_1", scope_slug="a", day=9, measurement="second"),
        _fact("fins_2", scope_slug="b", day=2, measurement="first"),
    ]

    sweeps = compute_sweeps(facts, routine_name="nightly", scope_slugs=["a", "b"], since=_SINCE, until=_UNTIL)

    assert [m.measurement for m in sweeps.measurements] == ["first", "second"]


def test_a_set_with_no_measurement_contributes_no_reading() -> None:
    facts = [_fact("fins_1", scope_slug="blizzard", day=2, measurement=None)]

    sweeps = compute_sweeps(facts, routine_name="nightly", scope_slugs=["blizzard"], since=_SINCE, until=_UNTIL)

    assert sweeps.measurements == []


def test_a_retired_scope_this_routine_has_swept_is_still_listed() -> None:
    """D3: `scope_slugs` carries only non-retired scopes — a retired scope must still
    surface if `facts` names it."""
    facts = [_fact("fins_1", scope_slug="retired-scope", day=2)]

    sweeps = compute_sweeps(facts, routine_name="nightly", scope_slugs=["live-scope"], since=_SINCE, until=_UNTIL)

    assert {row.scope_slug for row in sweeps.last_swept} == {"live-scope", "retired-scope"}
    live, retired = sorted(sweeps.last_swept, key=lambda r: r.scope_slug)
    assert live.finding_set_id is None
    assert retired.finding_set_id == "fins_1"


def test_rows_are_sorted_by_scope_slug() -> None:
    facts = [_fact("fins_1", scope_slug="zeta", day=2), _fact("fins_2", scope_slug="alpha", day=2)]

    sweeps = compute_sweeps(
        facts, routine_name="nightly", scope_slugs=["zeta", "alpha", "mid"], since=_SINCE, until=_UNTIL
    )

    assert [row.scope_slug for row in sweeps.last_swept] == ["alpha", "mid", "zeta"]
