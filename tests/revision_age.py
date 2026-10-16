"""Retire a store-revision test by the age of the revision its filename names."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from tests.repo_files import repo_root

#: Set to ``1`` to run every colocated store-migration test, however old its revision.
RUN_RETIRED_ENV = "BLIZZARD_RUN_RETIRED_MIGRATION_TESTS"

#: A revision older than this many days is retired from the default run.
RETIREMENT_DAYS = 30

_STEM = re.compile(r"^(\d{8})_\d{4}_")

_VERSIONS = ("hub", "runner")


#: A colocated test file's name: the stem of the revision it proves, then the ``_test`` suffix.
_TEST_FILE = re.compile(r"^(?P<stem>\d{8}_\d{4}_.+)_test\.py$")

#: The stores whose revisions retire by age; the exemplar's colocated test is held to the naming rule only.
_SHIPPED_STORES = tuple(Path("src") / "blizzard" / component / "store" / "migrations" for component in _VERSIONS)


class InvalidRevisionTest(ValueError):
    """A colocated test file that names no revision in its sibling ``versions/`` directory."""


@dataclass(frozen=True)
class Retired:
    """The decision to skip a test, with the reason the skip reports."""

    reason: str


def colocated_versions(path: Path) -> Path | None:
    """The ``versions/`` directory beside a ``migrations/tests/`` directory holding ``path``, else ``None``."""
    tests_dir = path.parent
    if tests_dir.name != "tests" or tests_dir.parent.name != "migrations":
        return None
    versions = tests_dir.parent / "versions"
    return versions if versions.is_dir() else None


def decide_colocated(
    path: Path,
    *,
    today: date,
    environ: Mapping[str, str],
    root: Path | None = None,
) -> Retired | None:
    """``None`` to run a colocated test file, ``Retired`` to skip it; raises ``InvalidRevisionTest`` on a bad name.

    The filename is the only tie to a revision: its stem, before the ``_test`` suffix, must name a revision in the
    sibling ``versions/`` directory. Only the shipped stores' tests retire by age; the override runs them all.
    """
    versions = colocated_versions(path)
    if versions is None:
        raise InvalidRevisionTest(f"{path} is not in a migrations/tests/ directory beside a versions/ directory")
    match = _TEST_FILE.match(path.name)
    if match is None:
        raise InvalidRevisionTest(f"{path.name!r} is not named <YYYYMMDD_HHMM_revision-stem>_test.py")
    stem = match.group("stem")
    if not (versions / f"{stem}.py").is_file():
        raise InvalidRevisionTest(f"{stem!r} names no revision in {versions}")
    base = repo_root() if root is None else root
    if not any(path.parent.parent == base / store for store in _SHIPPED_STORES):
        return None
    if environ.get(RUN_RETIRED_ENV) == "1":
        return None
    prefix = _STEM.match(stem)
    assert prefix is not None  # _TEST_FILE guarantees the prefix
    age = (today - datetime.strptime(prefix.group(1), "%Y%m%d").date()).days
    if age > RETIREMENT_DAYS:
        return Retired(f"revision {stem} is {age} days old; set {RUN_RETIRED_ENV}=1 to run it")
    return None
