"""Keep graduated writers off the legacy chunk-exclusive handle."""

import ast

import pytest

from tests.support.repo_files import repo_root

_HUB = repo_root() / "src/blizzard/hub"
_CONVERTED = (
    "domain/execution/apply.py",
    "domain/execution/decisions.py",
    "domain/operations/restart.py",
    "domain/operations/queue.py",
    "delivery/hub_node.py",
)
_LEGACY = {"IChunkExclusiveWrites", "ILockedChunkRead", "_group_locked", "_escalate_locked"}


def _violations(source: str) -> list[int]:
    violations: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if (
            (isinstance(node, ast.ImportFrom) and node.module == "blizzard.hub.domain.chunk.ports.exclusive")
            or (
                isinstance(node, ast.Import)
                and any(a.name == "blizzard.hub.domain.chunk.ports.exclusive" for a in node.names)
            )
            or (isinstance(node, ast.Name) and node.id in _LEGACY)
            or (isinstance(node, ast.Attribute) and node.attr in _LEGACY | {"_exclusive"})
            or (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _LEGACY)
        ):
            violations.append(node.lineno)
    return violations


def _exclusive_callers(source: str) -> list[int]:
    tree = ast.parse(source)
    aliases = {"IChunkExclusiveWrites"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            aliases.update(a.asname or a.name for a in node.names if a.name == "IChunkExclusiveWrites")
    typed: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and isinstance(node.annotation, ast.Name) and node.annotation.id in aliases:
            typed.add(node.arg)
        elif (
            isinstance(node, ast.AnnAssign) and isinstance(node.annotation, ast.Name) and node.annotation.id in aliases
        ):
            if isinstance(node.target, ast.Name):
                typed.add(node.target.id)
            elif isinstance(node.target, ast.Attribute):
                typed.add(node.target.attr)
    result: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "locked":
            receiver = node.func.value
            name = (
                receiver.id
                if isinstance(receiver, ast.Name)
                else receiver.attr
                if isinstance(receiver, ast.Attribute)
                else ""
            )
            if name in typed | {"exclusive", "_exclusive", "chunk_exclusive", "_chunk_exclusive"}:
                result.append(node.lineno)
    return result


@pytest.mark.parametrize("path", _CONVERTED)
def test_converted_writers_do_not_reintroduce_chunk_handles(path: str) -> None:
    assert _violations((_HUB / path).read_text()) == []


def test_production_chunk_exclusive_caller_census_is_empty() -> None:
    callers = {
        str(path.relative_to(_HUB)): lines
        for path in _HUB.rglob("*.py")
        if (lines := _exclusive_callers(path.read_text()))
    }
    assert callers == {}


@pytest.mark.parametrize(
    "source",
    [
        "from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead as Read",
        "import blizzard.hub.domain.chunk.ports.exclusive as old",
        "self._exclusive.locked(ids)",
        "def _group_locked(): pass",
        "self._escalate_locked()",
    ],
)
def test_guard_detects_legacy_forms(source: str) -> None:
    assert _violations(source)


def test_census_detects_port_calls_but_not_other_locks_or_definitions() -> None:
    source = """\
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites as Writes
def operate(port: Writes):
    port.locked(ids)
    self._exclusive.locked(ids)
    self.graph_lock.locked(ids)
class Adapter:
    def locked(self, ids): pass
"""
    assert _exclusive_callers(source) == [3, 4]
