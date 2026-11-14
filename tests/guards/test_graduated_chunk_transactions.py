"""Keep the hub free of ``_locked`` helpers and ``ILocked*`` types — a locked unit is a ``LockedTransaction``."""

import ast

from tests.support.repo_files import repo_root

_HUB = repo_root() / "src/blizzard/hub"


def _is_locked_type(name: str) -> bool:
    return name.startswith("ILocked")


def _violations(source: str) -> list[int]:
    violations: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if (
            (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.endswith("_locked"))
            or (isinstance(node, ast.ClassDef) and _is_locked_type(node.name))
            or (isinstance(node, ast.Name) and _is_locked_type(node.id))
            or (isinstance(node, ast.Attribute) and _is_locked_type(node.attr))
            or (
                isinstance(node, (ast.Import, ast.ImportFrom))
                and any(_is_locked_type(alias.name.rsplit(".", 1)[-1]) for alias in node.names)
            )
            or (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.isidentifier()
                and _is_locked_type(node.value)
            )
        ):
            violations.append(node.lineno)
    return violations


def test_hub_defines_no_locked_helpers_or_locked_types() -> None:
    found = {
        str(path.relative_to(_HUB)): lines
        for path in sorted(_HUB.rglob("*.py"))
        if (lines := _violations(path.read_text()))
    }
    assert found == {}


def test_guard_fails_on_planted_locked_helper_and_type() -> None:
    assert _violations("def record_stop_locked(self):\n    pass\n") == [1]
    assert _violations("async def x_locked():\n    pass\n") == [1]
    assert _violations("class ILockedChunkRead:\n    pass\n") == [1]
    assert _violations("from blizzard.hub.ports import ILockedThing as Read\n") == [1]
    assert _violations("def f(read: ILockedThing) -> None:\n    pass\n") == [1]
    assert _violations("def f(read: 'ILockedThing') -> None:\n    pass\n") == [1]


def test_guard_passes_on_clean_source() -> None:
    source = """\
from blizzard.hub.domain.kernel.transactions import LockedTransaction
def record_stop(chunk_id: str, *, transaction: LockedTransaction) -> None:
    keys_locked = []
    self.graph_lock.locked(ids)
"""
    assert _violations(source) == []
