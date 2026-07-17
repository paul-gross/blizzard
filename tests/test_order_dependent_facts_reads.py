"""Structural guard: an order-dependent facts field carries an explicit `order_by` on its
store-side select (``bzh:sql-portable``, issue #48).

Staying inside SQLAlchemy's portable *syntax* is not enough to guarantee a *deterministic*
result: without an explicit total ``order_by``, postgres is free to return a select's rows
in any order, while sqlite's incidental rowid order happens to preserve insertion order.
A domain consumer that indexes a facts field with ``[-1]``/``[0]`` — rather than deriving
the newest/oldest fact itself via ``max``/``sorted`` — silently depends on an ordering
guarantee its store-side query never made, and no sqlite-backed test can ever see that
omission break.

AST-walks (not grep: the shapes below span statements) two layers, mirroring
``test_wire_timestamps.py``'s structural-guard shape:

1. Every domain module (``*/domain/*.py``, hub and runner) for an ``x.NAME[-1]`` or
   ``x.NAME[0]`` subscript — the shape a consumer takes when it depends on a facts
   field's row order without deriving it defensively itself. Collects the field names
   (``NAME``) this pattern is found on — currently just ``pauses``
   (``derive_chunk_status``'s ``facts.pauses[-1]``, via ``open_pause``). This collection
   is deliberately over-broad: it takes *any* ``x.NAME[-1]``/``[0]`` attribute subscript
   in the domain layer, not just ones proven to read a facts field, so an unrelated
   ``self.history[-1]`` on some non-facts object would seed a field name this guard then
   polices in step 2. That risks a false-positive violation on a store-internal variable
   that happens to share the name but isn't the same field — judged acceptable next to
   the cost of resolving each subscript's receiver back to a facts type, which the AST
   alone can't do without a type checker in the loop.
2. Every store-internal module (``*/store/internal/*.py``, hub and runner) for a local
   variable, named identically to one of those fields, assigned from an expression that
   calls ``select(...)``. When that expression carries no ``.order_by(...)`` call
   anywhere in it, the store reader is the field's order-dependent source and the guard
   fails, naming the assignment site.

Scoped to the variable-name-matches-field-name shape every ``load_facts``-style reader in
this codebase uses consistently (``chunk_store.load_facts`` is the reference instance,
e.g. its local ``pauses = [...]`` feeding ``ChunkFacts(..., pauses=pauses, ...)``) — a
reader that builds the same list under a different local variable name than the field
it's assigned to escapes this guard. That narrower gap is judged acceptable next to the
false-positive risk of a blanket "every select needs an order_by" rule, which would fail
legitimate unordered reads (existence checks, aggregate counts, set-builders) that make up
most of the same files.
"""

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
