"""No two tests carry the same body under different names (unit tier).

A case whose body matches a sibling's asserts only what the sibling already asserts,
so its own name is a claim nothing observes — the vacuous-duplicate shape issue #275
swept for. A deliberate cross-tier re-run is declared below.
"""

from __future__ import annotations

import ast
import collections
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_TESTS = Path(__file__).resolve().parent

#: Bodies duplicated on purpose, keyed by test name — the parity guard's endpoint-table
#: check is re-run under the service tier's own gate, off one shared helper.
_DECLARED_RERUNS = {"test_ihubclient_endpoint_table_matches_the_protocol_method_set"}


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level constant assignments, dumped — two modules whose identically-shaped
    bodies read a same-named constant of *different* value are not duplicates."""
    constants: dict[str, str] = {}
    for node in tree.body:
        targets = (
            [node.target] if isinstance(node, ast.AnnAssign) else node.targets if isinstance(node, ast.Assign) else []
        )
        value = getattr(node, "value", None)
        for target in targets:
            if isinstance(target, ast.Name) and value is not None:
                constants[target.id] = ast.dump(value)
    return constants


def _bodies() -> dict[tuple[str, str], list[str]]:
    """Every test function keyed by ``(name, normalized body)`` — docstrings dropped,
    since a differing docstring is exactly how a duplicate hides."""
    found: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for path in sorted(_TESTS.rglob("test_*.py")):
        tree = ast.parse(path.read_text())
        constants = _module_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test_"):
                continue
            body = [n for n in node.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
            if not body:
                continue
            dumped = ast.dump(ast.Module(body=body, type_ignores=[]))
            decorators = ast.dump(ast.Module(body=[ast.Expr(d) for d in node.decorator_list], type_ignores=[]))
            read = sorted(
                f"{name}={value}"
                for name, value in constants.items()
                if any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))
            )
            key = (node.name, dumped + decorators + "".join(read))
            found[key].append(f"{path.relative_to(_TESTS)}::{node.name}")
    return found


def test_no_two_cases_share_a_body() -> None:
    by_body: dict[str, list[str]] = collections.defaultdict(list)
    for (name, body), locations in _bodies().items():
        if name in _DECLARED_RERUNS:
            continue
        by_body[body].extend(locations)
    duplicates = {body: names for body, names in by_body.items() if len(names) > 1}
    assert not duplicates, "\n".join(
        [
            "cases sharing a body — each name claims something only one of them pins:",
            *(str(v) for v in duplicates.values()),
        ]
    )
