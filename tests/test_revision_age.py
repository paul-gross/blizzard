"""A ``migration_revision`` test retires once its revision is more than 30 days old."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tests.revision_age import (
    RUN_RETIRED_ENV,
    InvalidRevisionMarker,
    InvalidRevisionTest,
    Retired,
    colocated_versions,
    decide,
    decide_colocated,
    revision_index,
)

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


_OLD = "20260915_1000_old_enough"
_NEW = "20260916_1000_recent"


def _tree(root: Path, *, store: str = "src/blizzard/hub/store/migrations") -> Path:
    """A ``migrations`` directory holding both revisions; returns its ``tests`` directory."""
    versions = root / store / "versions"
    versions.mkdir(parents=True)
    for stem in (_OLD, _NEW):
        (versions / f"{stem}.py").write_text("")
    tests = root / store / "tests"
    tests.mkdir()
    return tests


def test_a_colocated_test_of_a_revision_over_thirty_days_old_is_retired(tmp_path: Path) -> None:
    path = _tree(tmp_path) / f"{_OLD}_test.py"
    decision = decide_colocated(path, today=_TODAY, environ={}, root=tmp_path)
    assert isinstance(decision, Retired)
    assert _OLD in decision.reason
    assert RUN_RETIRED_ENV in decision.reason


def test_a_colocated_test_of_a_revision_exactly_thirty_days_old_runs(tmp_path: Path) -> None:
    path = _tree(tmp_path) / f"{_NEW}_test.py"
    assert decide_colocated(path, today=_TODAY, environ={}, root=tmp_path) is None


def test_the_override_runs_a_retired_colocated_test(tmp_path: Path) -> None:
    path = _tree(tmp_path) / f"{_OLD}_test.py"
    assert decide_colocated(path, today=_TODAY, environ={RUN_RETIRED_ENV: "1"}, root=tmp_path) is None


def test_a_runner_store_test_retires_the_same_way(tmp_path: Path) -> None:
    path = _tree(tmp_path, store="src/blizzard/runner/store/migrations") / f"{_OLD}_test.py"
    assert isinstance(decide_colocated(path, today=_TODAY, environ={}, root=tmp_path), Retired)


def test_a_test_beside_a_revision_outside_the_shipped_stores_never_retires(tmp_path: Path) -> None:
    path = _tree(tmp_path, store="exemplars/python/exemplar/hub/store/migrations") / f"{_OLD}_test.py"
    assert decide_colocated(path, today=_TODAY, environ={}, root=tmp_path) is None


@pytest.mark.parametrize("name", ["initial_schema_test.py", "20260915_old_test.py", f"{_OLD}.py", f"test_{_OLD}.py"])
def test_a_colocated_file_not_named_for_a_dated_revision_is_refused(tmp_path: Path, name: str) -> None:
    path = _tree(tmp_path) / name
    with pytest.raises(InvalidRevisionTest):
        decide_colocated(path, today=_TODAY, environ={}, root=tmp_path)


def test_a_colocated_file_naming_no_sibling_revision_is_refused(tmp_path: Path) -> None:
    path = _tree(tmp_path) / "20260915_1000_missing_test.py"
    with pytest.raises(InvalidRevisionTest):
        decide_colocated(path, today=_TODAY, environ={RUN_RETIRED_ENV: "1"}, root=tmp_path)


def test_a_stem_present_only_in_the_other_stores_versions_is_refused(tmp_path: Path) -> None:
    hub_tests = _tree(tmp_path)
    runner_versions = tmp_path / "src/blizzard/runner/store/migrations/versions"
    runner_versions.mkdir(parents=True)
    (runner_versions / "20260914_1000_runner_only.py").write_text("")
    with pytest.raises(InvalidRevisionTest):
        decide_colocated(hub_tests / "20260914_1000_runner_only_test.py", today=_TODAY, environ={}, root=tmp_path)


def test_only_a_migrations_tests_directory_beside_versions_is_colocated(tmp_path: Path) -> None:
    tests = _tree(tmp_path)
    assert colocated_versions(tests / f"{_OLD}_test.py") == tests.parent / "versions"
    assert colocated_versions(tests.parent / "env.py") is None
    assert colocated_versions(tmp_path / "tests" / "test_x.py") is None
