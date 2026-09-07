from __future__ import annotations

import itertools
import re
from pathlib import Path
from typing import cast, get_args

import pytest

from blizzard.foundation.event_log import EVENT_LOG_SEVERITY, EventLogKind
from blizzard.hub.domain.work import SEVERITY_RANK
from tests.event_log_kind_census import EVENT_LOG_KIND_CENSUS as CENSUS
from tests.event_log_kind_census import Projected, Recorded

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OPERATIONS_MD = _REPO_ROOT.parent / "blizzard-context" / "domain" / "operations.md"


def test_census_names_exactly_the_declared_kinds() -> None:
    assert set(CENSUS) == set(get_args(EventLogKind))


def test_every_census_severity_matches_the_vocabulary_and_the_closed_rank() -> None:
    for kind, disposition in CENSUS.items():
        assert disposition.severity == EVENT_LOG_SEVERITY[cast(EventLogKind, kind)]
        assert disposition.severity in SEVERITY_RANK


def test_every_disposition_is_recorded_or_projected_with_a_real_site() -> None:
    for disposition in CENSUS.values():
        assert isinstance(disposition, (Recorded, Projected))
        assert disposition.where


def _event_kinds_table() -> list[tuple[str, str]]:
    """The ``### Event kinds`` table's ``(Kind, Severity)`` rows, in document order — bounded
    to this one table, not every ``|``-prefixed line for the rest of the file, so a later
    table added anywhere below cannot silently join the parse."""
    text = _OPERATIONS_MD.read_text()
    section = text.split("### Event kinds", 1)[1]
    lines = section.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("|"))
    table_lines = list(itertools.takewhile(lambda line: line.startswith("|"), lines[start:]))
    rows: list[tuple[str, str]] = []
    for line in table_lines[1:]:  # skip the header row; its underline comes next
        cells = [c.strip() for c in line.strip("|").split("|")]
        if re.fullmatch(r":?-+:?", cells[0]):
            continue  # the header underline row
        rows.append((cells[0].strip("`"), cells[1].strip("`")))
    return rows


def test_the_domain_table_binds_exactly_the_declared_kinds() -> None:
    """This test reads only the ``blizzard-context`` sibling — never ``blizzard-mock`` too,
    which ``test_case12c_committed_registry_cross_repo_agreement`` also turns on but which
    this check never touches — so it must not borrow that test's wider skip condition."""
    if not _OPERATIONS_MD.is_file():
        pytest.skip(
            "sibling blizzard-context worktree absent — the domain table binding is unchecked "
            "here; covered locally and by every feature-env run that carries the sibling"
        )
    table = _event_kinds_table()
    assert {kind for kind, _severity in table} == set(get_args(EventLogKind))
    for kind, severity in table:
        assert severity == EVENT_LOG_SEVERITY[cast(EventLogKind, kind)]
