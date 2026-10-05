"""The Protocol seam-size gate (``bzh:seam-size-ceiling``): a ``ClassDef`` counts
iff ``Protocol`` is one of its own bases (matched by ``test_layering.py``'s
``_protocol_declarations``), and must declare at most ``_SEAM_SIZE_LIMIT`` own methods.
``_ACCEPTED_VIOLATIONS`` names today's exceptions; the gate fails on an unregistered violation
and on a stale entry alike. A composed alias (no method re-declared in its own body) counts
zero own methods — a consumer's own re-typing is a review obligation the gate cannot see. A
Protocol base nothing else names — a split with no consumer of its own — counts toward each
Protocol composing it, so splitting a seam only to clear the ceiling fails the gate."""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_REPO_ROOT = repo_root()
_SRC_DIR = _REPO_ROOT / "src" / "blizzard"

_SEAM_SIZE_LIMIT = 12

#: ``SpawnContext`` (14): ``Spawner`` builds an ``Attempt`` and ``Attempt`` builds a ``Spawner``,
#: each handing the other its own ``ctx``, so the two consumers share one context — the union of
#: what either reads. Splitting it follows no consumer line until that cycle is broken.
_ACCEPTED_VIOLATIONS: set[str] = {"SpawnContext"}


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


def _protocol_widths(root: Path) -> dict[str, int]:
    """Each Protocol's width: its own methods, plus — recursively — those of every Protocol base no
    code names except as a base, resolved in the composing module first, else by a tree-unique name."""
    by_module: dict[Path, dict[str, ast.ClassDef]] = {}
    named: Counter[str] = Counter()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        bases = {id(base) for node in ast.walk(tree) if isinstance(node, ast.ClassDef) for base in node.bases}
        for node in ast.walk(tree):
            if id(node) in bases:
                continue
            if isinstance(node, ast.Name):
                named[node.id] += 1
            elif isinstance(node, ast.Attribute):
                named[node.attr] += 1
        by_module[path] = {
            node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and _is_protocol(node)
        }
    everywhere = Counter(name for protocols in by_module.values() for name in protocols)
    unique = {
        name: node for protocols in by_module.values() for name, node in protocols.items() if everywhere[name] == 1
    }

    def width(node: ast.ClassDef, module: dict[str, ast.ClassDef]) -> int:
        composed = 0
        for base in node.bases:
            name = base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute) else None
            split = module.get(name or "") or unique.get(name or "")
            if split is not None and split is not node and named[split.name] == 0:
                composed += width(split, module)
        return _own_method_count(node) + composed

    return {name: width(node, protocols) for protocols in by_module.values() for name, node in protocols.items()}


def _oversized_protocols(root: Path) -> set[str]:
    return {name for name, width in _protocol_widths(root).items() if width > _SEAM_SIZE_LIMIT}


def _plant_protocols(tmp_path: Path, text: str) -> set[str]:
    (tmp_path / "seams.py").write_text(text)
    return _oversized_protocols(tmp_path)


def _members(prefix: str, count: int) -> str:
    return "".join(f"    def {prefix}{index}(self) -> int: ...\n" for index in range(count))


def test_a_consumerless_split_counts_toward_the_protocol_composing_it(tmp_path: Path) -> None:
    split = f"class Half(Protocol):\n{_members('a', 7)}\nclass Whole(Half, Protocol):\n{_members('b', 7)}"
    assert _plant_protocols(tmp_path, split) == {"Whole"}


def test_a_split_a_consumer_names_counts_on_its_own(tmp_path: Path) -> None:
    split = f"class Half(Protocol):\n{_members('a', 7)}\nclass Whole(Half, Protocol):\n{_members('b', 7)}"
    assert _plant_protocols(tmp_path, f"{split}\ndef use(ctx: Half) -> None: ...\n") == set()


def test_a_protocol_over_the_limit_by_its_own_methods_is_caught(tmp_path: Path) -> None:
    assert _plant_protocols(tmp_path, f"class Wide(Protocol):\n{_members('a', 13)}") == {"Wide"}


def test_no_protocol_exceeds_the_seam_size_limit_beyond_the_registered_exceptions() -> None:
    """Any Protocol wider than a dozen own methods is a violation unless named in
    ``_ACCEPTED_VIOLATIONS`` — the registry each later phase that narrows a seam empties
    its own entry from."""
    assert _oversized_protocols(_SRC_DIR) == _ACCEPTED_VIOLATIONS
