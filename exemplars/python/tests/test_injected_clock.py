"""The ``bzh:injected-clock`` guard: a live scan of the exemplar tree, and the planted trees that prove the scan."""

# exemplar: bzh:injected-clock, bzh:case-pins-its-own-name — a mechanical check that proves its own reach.

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_EXEMPLAR_ROOT = Path(__file__).parents[1] / "exemplar"

#: The clock implementation every ``IClock.now()`` reaches — the one module that reads the wall clock.
_CLOCK_MODULE = Path("foundation") / "clock.py"

_WALL_CLOCK_READS: frozenset[str] = frozenset(
    {
        "datetime.datetime.now",
        "datetime.datetime.utcnow",
        "datetime.date.today",
        "time.time",
        "sqlalchemy.func.now",
    }
)


def _import_bindings(tree: ast.Module) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bindings[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                bindings[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return bindings


def _qualified_name(func: ast.expr, bindings: dict[str, str]) -> str | None:
    attributes: list[str] = []
    while isinstance(func, ast.Attribute):
        attributes.append(func.attr)
        func = func.value
    if not isinstance(func, ast.Name):
        return None
    return ".".join([bindings.get(func.id, func.id), *reversed(attributes)])


def _wall_clock_reads(root: Path) -> list[str]:
    """Every wall-clock read under ``root`` outside the clock module — a call or a bare reference, such as a column
    default — as ``file:line name``."""
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if relative == _CLOCK_MODULE:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        bindings = _import_bindings(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name | ast.Attribute) and isinstance(node.ctx, ast.Load):
                name = _qualified_name(node, bindings)
                if name in _WALL_CLOCK_READS:
                    violations.append(f"{relative}:{node.lineno} {name}")
    return violations


def _plant(root: Path, relative: str, source: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return root


def test_no_exemplar_module_reads_the_wall_clock_directly() -> None:
    assert _wall_clock_reads(_EXEMPLAR_ROOT) == []


@pytest.mark.parametrize(
    ("source", "caught"),
    [
        ("from datetime import datetime\nstamp = datetime.now()\n", "datetime.datetime.now"),
        ("from datetime import datetime\nstamp = datetime.utcnow()\n", "datetime.datetime.utcnow"),
        ("import datetime as dt\nstamp = dt.datetime.now()\n", "datetime.datetime.now"),
        ("import datetime\nday = datetime.date.today()\n", "datetime.date.today"),
        ("from datetime import date as day\ntoday = day.today()\n", "datetime.date.today"),
        ("import time\nstamp = time.time()\n", "time.time"),
        ("from time import time\nstamp = time()\n", "time.time"),
        ("from sqlalchemy import func\ndefault = func.now()\n", "sqlalchemy.func.now"),
    ],
)
def test_a_direct_wall_clock_read_is_caught_in_every_import_form(tmp_path: Path, source: str, caught: str) -> None:
    assert _wall_clock_reads(_plant(tmp_path, "hub/stamp.py", source)) == [f"hub/stamp.py:2 {caught}"]


@pytest.mark.parametrize(
    ("source", "caught"),
    [
        (
            "from datetime import datetime\nfrom sqlalchemy import Column, DateTime\n"
            "created_at = Column(DateTime, default=datetime.utcnow)\n",
            "hub/stamp.py:3 datetime.datetime.utcnow",
        ),
        ("from time import time\nstamp = time\n", "hub/stamp.py:2 time.time"),
    ],
)
def test_a_bare_reference_to_a_wall_clock_read_is_caught(tmp_path: Path, source: str, caught: str) -> None:
    assert _wall_clock_reads(_plant(tmp_path, "hub/stamp.py", source)) == [caught]


def test_the_declared_clock_module_may_read_the_wall_clock(tmp_path: Path) -> None:
    source = "from datetime import UTC, datetime\nstamp = datetime.now(UTC)\n"
    assert _wall_clock_reads(_plant(tmp_path, "foundation/clock.py", source)) == []


def test_a_clock_module_outside_the_declared_path_is_still_caught(tmp_path: Path) -> None:
    source = "from datetime import UTC, datetime\nstamp = datetime.now(UTC)\n"
    assert _wall_clock_reads(_plant(tmp_path, "hub/clock.py", source)) == ["hub/clock.py:2 datetime.datetime.now"]


def test_a_read_through_an_injected_clock_is_admitted(tmp_path: Path) -> None:
    source = "def stamp(clock: object) -> object:\n    return clock.now()\n"
    assert _wall_clock_reads(_plant(tmp_path, "hub/stamp.py", source)) == []


def test_a_comment_or_string_naming_a_wall_clock_read_is_admitted(tmp_path: Path) -> None:
    source = '"""Never ``datetime.now()`` here."""\n# nor time.time()\nNOTE = "date.today()"\n'
    assert _wall_clock_reads(_plant(tmp_path, "hub/stamp.py", source)) == []


def test_a_monotonic_read_is_not_a_wall_clock_read(tmp_path: Path) -> None:
    source = "import time\nstarted = time.monotonic()\n"
    assert _wall_clock_reads(_plant(tmp_path, "hub/stamp.py", source)) == []
