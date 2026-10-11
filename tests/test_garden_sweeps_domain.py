"""``compute_sweeps`` (unit tier) — the pure assembly of the store's newest-set-per-scope
rows and windowed measurements into the last-swept table. No store — plain lists of
``SweepFact`` and ``MeasurementReading`` in, a ``GardenSweeps`` out."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.garden.runs.sweeps import MeasurementReading, SweepFact, compute_sweeps

pytestmark = pytest.mark.unit

_SINCE = datetime(2026, 1, 1, tzinfo=UTC)
_UNTIL = datetime(2026, 1, 15, tzinfo=UTC)


def _fact(finding_set_id: str, *, scope_slug: str, day: int, revisions: dict[str, str] | None = None) -> SweepFact:
    return SweepFact(
        finding_set_id=finding_set_id,
        scope_slug=scope_slug,
        produced_at=datetime(2026, 1, day, tzinfo=UTC),
        revisions=revisions or {},
    )


def test_a_scope_with_no_set_reads_never() -> None:
    sweeps = compute_sweeps([], [], routine_name="nightly", scope_slugs=["blizzard"], since=_SINCE, until=_UNTIL)

    (row,) = sweeps.last_swept
    assert row.scope_slug == "blizzard"
    assert row.finding_set_id is None
    assert row.produced_at is None
    assert row.revisions == {}


def test_a_scope_with_a_set_reports_it() -> None:
    facts = [_fact("fins_2", scope_slug="blizzard", day=9, revisions={"blizzard": "bbb"})]

    sweeps = compute_sweeps(facts, [], routine_name="nightly", scope_slugs=["blizzard"], since=_SINCE, until=_UNTIL)

    (row,) = sweeps.last_swept
    assert row.finding_set_id == "fins_2"
    assert row.produced_at == datetime(2026, 1, 9, tzinfo=UTC)
    assert row.revisions == {"blizzard": "bbb"}


def test_the_measurements_pass_through_in_the_order_given() -> None:
    readings = [
        MeasurementReading(scope_slug="b", produced_at=datetime(2026, 1, 2, tzinfo=UTC), measurement="first"),
        MeasurementReading(scope_slug="a", produced_at=datetime(2026, 1, 9, tzinfo=UTC), measurement="second"),
    ]

    sweeps = compute_sweeps([], readings, routine_name="nightly", scope_slugs=["a", "b"], since=_SINCE, until=_UNTIL)

    assert sweeps.measurements == readings


def test_a_retired_scope_this_routine_has_swept_is_still_listed() -> None:
    """`scope_slugs` carries only the routine's declared set, retired scopes
    already filtered out by the caller — a retired scope must still surface if `newest`
    names it."""
    facts = [_fact("fins_1", scope_slug="retired-scope", day=2)]

    sweeps = compute_sweeps(facts, [], routine_name="nightly", scope_slugs=["live-scope"], since=_SINCE, until=_UNTIL)

    assert {row.scope_slug for row in sweeps.last_swept} == {"live-scope", "retired-scope"}
    live, retired = sorted(sweeps.last_swept, key=lambda r: r.scope_slug)
    assert live.finding_set_id is None
    assert retired.finding_set_id == "fins_1"


def test_rows_are_sorted_by_scope_slug() -> None:
    facts = [_fact("fins_1", scope_slug="zeta", day=2), _fact("fins_2", scope_slug="alpha", day=2)]

    sweeps = compute_sweeps(
        facts, [], routine_name="nightly", scope_slugs=["zeta", "alpha", "mid"], since=_SINCE, until=_UNTIL
    )

    assert [row.scope_slug for row in sweeps.last_swept] == ["alpha", "mid", "zeta"]
