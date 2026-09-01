"""The repository-seam size gate (AC1, blizzard#411 Phase 1).

AST-walks ``src/blizzard/`` for repository-seam Protocols — those named
``IRead<Concept>Repository`` / ``IWrite<Concept>Repository`` — and asserts each declares at
most ``_SEAM_SIZE_LIMIT`` own methods. ``_ACCEPTED_VIOLATIONS`` names today's exceptions; a
later phase that narrows a seam removes its entry, so the gate fails both on an unregistered
new violation and on a stale entry left behind after its seam is fixed."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src" / "blizzard"

_SEAM_NAME = re.compile(r"^I(Read|Write).*Repository$")
_SEAM_SIZE_LIMIT = 12

# blizzard#411: Phase 2 empties the first two entries, Phase 4 the third.
_ACCEPTED_VIOLATIONS = {
    "IWriteChunkRepository",
    "IReadChunkRepository",
    "IReadLeaseRepository",
}


def _own_method_count(node: ast.ClassDef) -> int:
    return sum(
        1
        for member in node.body
        if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef) and not member.name.startswith("_")
    )


def _oversized_seams(root: Path) -> set[str]:
    oversized: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ClassDef)
                and _SEAM_NAME.match(node.name)
                and _own_method_count(node) > _SEAM_SIZE_LIMIT
            ):
                oversized.add(node.name)
    return oversized


def test_no_repository_seam_exceeds_the_size_limit_beyond_the_registered_exceptions() -> None:
    """AC1: a repository-seam Protocol wider than a dozen own methods is a violation unless
    named in ``_ACCEPTED_VIOLATIONS`` — the registry each later phase is required to empty."""
    assert _oversized_seams(_SRC_DIR) == _ACCEPTED_VIOLATIONS
