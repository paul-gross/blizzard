from __future__ import annotations

import ast
import itertools
import re
from pathlib import Path
from typing import cast, get_args

import pytest

from blizzard.foundation.event_log import EVENT_LOG_SEVERITY, EventLogKind, EventLogSeverity
from blizzard.hub.domain.chunk.model import PROJECTED_EVENT_KINDS
from blizzard.hub.domain.observability.hub_events import HUB_EVENT_SEVERITY, HubEventKind
from tests.event_log_kind_census import EVENT_LOG_KIND_CENSUS as CENSUS
from tests.event_log_kind_census import HUB_EVENT_KIND_CENSUS as HUB_CENSUS
from tests.event_log_kind_census import HubOnly, Projected, Recorded
from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_REPO_ROOT = repo_root()
_OPERATIONS_MD = _REPO_ROOT.parent / "blizzard-context" / "domain" / "operations.md"


def test_census_names_exactly_the_declared_kinds() -> None:
    assert set(CENSUS) == set(get_args(EventLogKind))


def test_the_projected_dispositions_are_exactly_the_hubs_projected_kinds() -> None:
    projected = {kind for kind, disposition in CENSUS.items() if isinstance(disposition, Projected)}
    assert projected == set(PROJECTED_EVENT_KINDS)


def test_every_census_severity_matches_the_vocabulary() -> None:
    for kind, disposition in CENSUS.items():
        assert disposition.severity == EVENT_LOG_SEVERITY[cast(EventLogKind, kind)]
        assert disposition.severity in EventLogSeverity


def test_every_disposition_is_recorded_or_projected_with_a_real_site() -> None:
    for disposition in CENSUS.values():
        assert isinstance(disposition, (Recorded, Projected, HubOnly))
        assert disposition.where


def _assert_site_exists(kind: str, site: str) -> None:
    """``path:Qualified.name`` resolves to a file under ``src/blizzard/`` defining its last name."""
    path, _, qualname = site.partition(":")
    source = (_REPO_ROOT / "src" / "blizzard" / path).read_text()
    name = qualname.rsplit(".", 1)[-1]
    assert re.search(rf"\b(def|class) {re.escape(name)}\b", source), f"{kind}: {site} names no definition"


def test_every_named_site_exists() -> None:
    for kind, disposition in CENSUS.items():
        for site in (disposition.where, *(disposition.also if isinstance(disposition, Recorded) else ())):
            _assert_site_exists(kind, site)
    for kind, sites in HUB_CENSUS.items():
        for site in sites:
            _assert_site_exists(kind, site)


def test_the_hub_census_names_exactly_the_hub_kinds() -> None:
    assert set(HUB_CENSUS) == set(get_args(HubEventKind))


def test_only_a_tenant_teardowns_hub_kinds_have_no_writer() -> None:
    assert {kind for kind, sites in HUB_CENSUS.items() if not sites} == {"tenant-closed", "tenant-deleted"}


def test_a_hub_kind_shared_with_the_tenant_log_keeps_one_severity() -> None:
    for kind in set(get_args(HubEventKind)) & set(get_args(EventLogKind)):
        assert HUB_EVENT_SEVERITY[cast(HubEventKind, kind)] == EVENT_LOG_SEVERITY[cast(EventLogKind, kind)], kind


def test_a_hub_only_kind_is_a_hub_kind_and_recorded_where_the_hub_census_says() -> None:
    for kind, disposition in CENSUS.items():
        if isinstance(disposition, HubOnly):
            assert disposition.where in HUB_CENSUS.get(kind, ()), kind


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """The module's own ``NAME = "text"`` and ``NAME: T = "text"`` bindings."""
    constants: dict[str, str] = {}
    for node in tree.body:
        targets = (
            node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            constants.update({target.id: value.value for target in targets if isinstance(target, ast.Name)})
    return constants


def _tenant_records_of_hub_kinds(path: Path) -> list[str]:
    """Each call in ``path`` shaped as the tenant event log's ``record`` — it names ``runner_id`` — whose ``kind`` is
    a hub kind, written literally or through a module constant."""
    tree = ast.parse(path.read_text())
    constants = _module_constants(tree)
    hub_kinds = set(get_args(HubEventKind))
    found: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "record"):
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        kind = keywords.get("kind")
        if "runner_id" not in keywords or kind is None:
            continue
        value = (
            kind.value
            if isinstance(kind, ast.Constant)
            else constants.get(kind.id)
            if isinstance(kind, ast.Name)
            else None
        )
        if value in hub_kinds:
            found.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno} records {value}")
    return found


def test_no_hub_site_records_a_hub_kind_in_a_tenants_event_log() -> None:
    """The hub's own occurrences go to ``hub_event_log`` alone: no hub module hands a hub kind to the tenant event log
    service. A runner's report of its own trace export reaches it as data, never as a literal kind."""
    hub = _REPO_ROOT / "src" / "blizzard" / "hub"
    found = [site for path in sorted(hub.rglob("*.py")) for site in _tenant_records_of_hub_kinds(path)]
    assert found == []


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
