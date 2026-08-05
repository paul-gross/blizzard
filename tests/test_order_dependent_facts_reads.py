"""Structural guard: an order-dependent facts field carries an explicit `order_by` on its
store-side select (``bzh:sql-portable``, issue #48).

Without an explicit ``order_by``, postgres may return rows in any order, while sqlite's
incidental rowid order preserves insertion order — a consumer indexing a facts field
with ``[-1]``/``[0]`` depends on a guarantee its store-side query never made."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src" / "blizzard"
_DOMAIN_DIRS = [_SRC_DIR / "hub" / "domain", _SRC_DIR / "runner" / "domain"]
_STORE_INTERNAL_DIRS = [_SRC_DIR / "hub" / "store" / "internal", _SRC_DIR / "runner" / "store" / "internal"]


def _is_last_or_first_index(node: ast.Subscript) -> bool:
    """True for a `[-1]` or `[0]` subscript — the order-dependent index shapes."""
    index = node.slice
    if isinstance(index, ast.Constant) and index.value == 0:
        return True
    return (
        isinstance(index, ast.UnaryOp)
        and isinstance(index.op, ast.USub)
        and isinstance(index.operand, ast.Constant)
        and index.operand.value == 1
    )


def _order_sensitive_field_names(root: Path) -> set[str]:
    """Every `x.NAME[-1]` / `x.NAME[0]` attribute name found under `root`."""
    names: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Subscript)
                and _is_last_or_first_index(node)
                and isinstance(node.value, ast.Attribute)
            ):
                names.add(node.value.attr)
    return names


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _contains_call_named(tree: ast.AST, name: str) -> bool:
    return any(isinstance(n, ast.Call) and _call_name(n.func) == name for n in ast.walk(tree))


def _unordered_field_sources(path: Path, field_names: set[str]) -> list[str]:
    """Assignments in `path` binding one of `field_names` to a `select(...)`-fed
    expression with no `.order_by(...)` anywhere in that expression."""
    tree = ast.parse(path.read_text(), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id not in field_names:
            continue
        if _contains_call_named(node.value, "select") and not _contains_call_named(node.value, "order_by"):
            violations.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno} ({node.targets[0].id})")
    return violations


def test_order_dependent_facts_reads_carry_an_explicit_order_by() -> None:
    field_names: set[str] = set()
    for domain_dir in _DOMAIN_DIRS:
        field_names |= _order_sensitive_field_names(domain_dir)
    assert field_names, (
        "no domain module indexes a facts field with [-1]/[0] — this guard has nothing "
        "to check; investigate before trusting a green run"
    )

    violations: list[str] = []
    for store_dir in _STORE_INTERNAL_DIRS:
        for path in sorted(store_dir.rglob("*.py")):
            violations.extend(_unordered_field_sources(path, field_names))
    assert not violations, (
        "a facts field a domain consumer indexes with [-1]/[0] is read from a `select(...)` "
        "with no explicit `order_by` — sqlite's incidental rowid order hides this, postgres "
        f"returns the rows unordered by contract (bzh:sql-portable): {violations}"
    )
