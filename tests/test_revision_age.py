"""A ``migration_revision`` test retires once its revision is more than 30 days old."""

from __future__ import annotations

from datetime import date

import pytest

from tests.revision_age import RUN_RETIRED_ENV, InvalidRevisionMarker, Retired, decide, revision_index

pytestmark = pytest.mark.unit

_TODAY = date(2026, 10, 16)
_INDEX = {"20260915_1000_old_enough": date(2026, 9, 15), "20260916_1000_recent": date(2026, 9, 16)}


def test_a_revision_over_thirty_days_old_is_retired() -> None:
    decision = decide(("20260915_1000_old_enough",), today=_TODAY, environ={}, index=_INDEX)
    assert isinstance(decision, Retired)
    assert "20260915_1000_old_enough" in decision.reason
    assert RUN_RETIRED_ENV in decision.reason


def test_a_revision_exactly_thirty_days_old_runs() -> None:
    assert decide(("20260916_1000_recent",), today=_TODAY, environ={}, index=_INDEX) is None


def test_the_override_runs_a_retired_revision() -> None:
    assert decide(("20260915_1000_old_enough",), today=_TODAY, environ={RUN_RETIRED_ENV: "1"}, index=_INDEX) is None


@pytest.mark.parametrize("value", ["", "0", "true", "yes"])
def test_only_the_value_one_overrides(value: str) -> None:
    decision = decide(("20260915_1000_old_enough",), today=_TODAY, environ={RUN_RETIRED_ENV: value}, index=_INDEX)
    assert isinstance(decision, Retired)


@pytest.mark.parametrize(
    "args",
    [(), ("a", "b"), (20260915,), ("initial_schema",), ("20260915_1000_missing",)],
)
def test_a_marker_naming_no_real_revision_is_refused(args: tuple[object, ...]) -> None:
    with pytest.raises(InvalidRevisionMarker):
        decide(args, today=_TODAY, environ={}, index=_INDEX)


def test_the_override_does_not_excuse_a_bad_marker() -> None:
    with pytest.raises(InvalidRevisionMarker):
        decide(("20260915_1000_missing",), today=_TODAY, environ={RUN_RETIRED_ENV: "1"}, index=_INDEX)


def test_the_index_holds_every_revision_of_both_stores() -> None:
    from tests.repo_files import repo_root

    on_disk = {
        path.stem
        for component in ("hub", "runner")
        for path in (repo_root() / "src" / "blizzard" / component / "store" / "migrations" / "versions").glob("2*.py")
    }
    assert on_disk <= set(revision_index())
    assert revision_index()["20260713_1112_initial_schema"] == date(2026, 7, 13)
