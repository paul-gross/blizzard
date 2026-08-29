"""Structural guard: the kernel/daemon layering `bzh:domain-core` assumes (unit tier).

Every import in this codebase is absolute (``from blizzard.x.y import Z``, never a
relative ``from . import``), so a plain AST walk over each module's top-level import
statements is enough to name every module a file depends on."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src" / "blizzard"
_FOUNDATION_DIR = _SRC_DIR / "foundation"
_HUB_DIR = _SRC_DIR / "hub"
_RUNNER_DIR = _SRC_DIR / "runner"


def _imported_modules(path: Path) -> set[str]:
    """Every dotted module named by a top-level ``import``/``from … import`` in ``path``."""
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            modules.add(node.module)
    return modules


def _violations(root: Path, forbidden_prefixes: tuple[str, ...]) -> list[str]:
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        for module in sorted(_imported_modules(path)):
            if any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden_prefixes):
                violations.append(f"{path.relative_to(_REPO_ROOT)} imports {module}")
    return violations


def test_foundation_imports_neither_daemon() -> None:
    """A — the kernel depends on nothing above it (``bzh:domain-core``)."""
    violations = _violations(_FOUNDATION_DIR, ("blizzard.hub", "blizzard.runner"))
    assert not violations, f"foundation must not import either daemon: {violations}"


def test_hub_does_not_import_runner() -> None:
    """B — true today; pinned so it stays true."""
    violations = _violations(_HUB_DIR, ("blizzard.runner",))
    assert not violations, f"hub must not import the runner: {violations}"
