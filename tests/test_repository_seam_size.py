"""The Protocol seam-size gate (AC1, widened by ``bzh:pluggable-seams``): a ``ClassDef`` counts
iff ``Protocol`` is one of its own bases (matched by ``test_layering.py``'s
``_protocol_declarations``), and must declare at most ``_SEAM_SIZE_LIMIT`` own methods.
``_ACCEPTED_VIOLATIONS`` names today's exceptions; the gate fails on an unregistered violation
and on a stale entry alike. A composed alias (no method re-declared in its own body) counts
zero own methods — a consumer's own re-typing is a review obligation the gate cannot see."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src" / "blizzard"

_SEAM_SIZE_LIMIT = 12

_ACCEPTED_VIOLATIONS: set[str] = {
    # LoopContext's downstream steps exercise 13 of IHubClient's 15 methods — genuinely wide
    # by design, not a seam left to narrow (blizzard-context:architecture/system-shape.md).
    "IHubClient",
}


def _is_protocol(node: ast.ClassDef) -> bool:
    return any(
        (isinstance(base, ast.Name) and base.id == "Protocol")
        or (isinstance(base, ast.Attribute) and base.attr == "Protocol")
        for base in node.bases
    )


def _own_method_count(node: ast.ClassDef) -> int:
    return sum(
        1
        for member in node.body
        if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef) and not member.name.startswith("_")
    )


def _oversized_protocols(root: Path) -> set[str]:
    oversized: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and _is_protocol(node) and _own_method_count(node) > _SEAM_SIZE_LIMIT:
                oversized.add(node.name)
    return oversized


def test_no_protocol_exceeds_the_seam_size_limit_beyond_the_registered_exceptions() -> None:
    """AC1: any Protocol wider than a dozen own methods is a violation unless named in
    ``_ACCEPTED_VIOLATIONS`` — the registry each later phase that narrows a seam empties
    its own entry from."""
    assert _oversized_protocols(_SRC_DIR) == _ACCEPTED_VIOLATIONS
