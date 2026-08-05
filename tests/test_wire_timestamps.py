"""The UTC-instants fitness test (issue #28, ``bzh:utc-instants``).

1. **Structural guard** — AST-walks every module under ``src/blizzard/`` for a call to
   ``.isoformat()`` (``foundation/store/utc.py`` excluded, ``iso_utc``'s own impl).
2. **Schema guard** — every ``DateTime``-family column in both stores is
   ``UtcDateTime``-typed."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import DateTime

from blizzard.foundation.store.utc import UtcDateTime
from blizzard.hub.store import schema as hub_schema
from blizzard.runner.store import schema as runner_schema

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src" / "blizzard"
# The one legitimate owner of a raw `.isoformat()` call — iso_utc's own implementation.
_EXCLUDED_FILES = {_SRC_DIR / "foundation" / "store" / "utc.py"}


def _isoformat_calls(path: Path) -> list[str]:
    """Every ``x.isoformat()`` call site in ``path``, as ``"line N"`` strings."""
    tree = ast.parse(path.read_text(), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "isoformat":
            found.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno}")
    return found


def test_no_raw_isoformat_at_an_api_edge() -> None:
    violations: list[str] = []
    for path in sorted(_SRC_DIR.rglob("*.py")):
        if path in _EXCLUDED_FILES:
            continue
        violations.extend(_isoformat_calls(path))
    assert not violations, (
        "raw `.isoformat()` in src/blizzard — a naive datetime reaches the wire unless "
        "the column is UtcDateTime-typed; use `iso_utc(...)` instead "
        f"(blizzard.foundation.store.utc.iso_utc): {violations}"
    )


def test_every_datetime_column_is_utc_datetime() -> None:
    violations: list[str] = []
    for schema in (hub_schema, runner_schema):
        for table in schema.metadata.tables.values():
            for column in table.columns:
                if isinstance(column.type, (DateTime, UtcDateTime)) and not isinstance(column.type, UtcDateTime):
                    violations.append(f"{schema.__name__}:{table.name}.{column.name}")
    assert not violations, f"DateTime column(s) not typed UtcDateTime: {violations}"
