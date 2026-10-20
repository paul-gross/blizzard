"""One store's migration tree, read as files: its revisions, their chain, and the baseline it may already carry."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.roles import domain_model


class FoldError(RuntimeError):
    """A fold the tool refuses, with the reason an operator acts on."""


@domain_model
@dataclass(frozen=True)
class Revision:
    """One revision file: its id, the revision it follows, and the ids it absorbed when it is a baseline."""

    revision: str
    down_revision: str | None
    path: Path
    absorbed: frozenset[str]


def _assigned(module: ast.Module, name: str) -> ast.expr | None:
    for node in module.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return node.value
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node.value
    return None


def _read(path: Path) -> Revision:
    module = ast.parse(path.read_text())
    revision = _assigned(module, "revision")
    down = _assigned(module, "down_revision")
    if not (isinstance(revision, ast.Constant) and isinstance(revision.value, str)) or down is None:
        raise FoldError(f"{path} does not declare a literal revision and down_revision")
    if not (isinstance(down, ast.Constant) and (down.value is None or isinstance(down.value, str))):
        raise FoldError(f"{path} has a down_revision that is not a single id; only a linear tree folds")
    absorbed = _assigned(module, "ABSORBED")
    ids: frozenset[str] = frozenset()
    if absorbed is not None:
        value = ast.literal_eval(absorbed.args[0]) if isinstance(absorbed, ast.Call) and absorbed.args else ()
        ids = frozenset(value)
    return Revision(revision.value, down.value, path, ids)


def read_tree(versions: Path) -> dict[str, Revision]:
    """Every revision under ``versions``, by id."""
    revisions: dict[str, Revision] = {}
    for path in sorted(versions.glob("*.py")):
        if path.name == "__init__.py":
            continue
        entry = _read(path)
        revisions[entry.revision] = entry
    return revisions


def base_of(tree: dict[str, Revision]) -> Revision:
    bases = [entry for entry in tree.values() if entry.down_revision is None]
    if len(bases) != 1:
        raise FoldError(f"the tree has {len(bases)} base revisions; a fold needs exactly one")
    return bases[0]


def ancestors(tree: dict[str, Revision], revision: str) -> list[Revision]:
    """The revisions strictly below ``revision``, nearest first."""
    chain: list[Revision] = []
    current = tree[revision].down_revision
    while current is not None:
        if current not in tree:
            raise FoldError(f"revision {current} is named as a down_revision but no file declares it")
        chain.append(tree[current])
        current = tree[current].down_revision
    return chain
