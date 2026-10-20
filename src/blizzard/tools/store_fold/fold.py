"""Fold each store tree up to a floor release's head revision: one baseline, the history beneath it deleted."""

from __future__ import annotations

import ast
import json
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from blizzard.foundation.roles import domain_model
from blizzard.tools.compat.git import Repository
from blizzard.tools.release_install import Installation, download_release, install
from blizzard.tools.store_fold.baseline import write_baseline
from blizzard.tools.store_fold.history import history_store, render
from blizzard.tools.store_fold.tree import FoldError, ancestors, base_of, read_tree

TREES = ("hub", "runner")

#: A tree's migrations directory, relative to the repository root.
_MIGRATIONS = "src/blizzard/{tree}/store/migrations"

#: What renders a tree's floor history: ``(tree) -> the floor-side renderer's document``.
type Renderer = Callable[[str], Mapping[str, Any]]


@domain_model
@dataclass(frozen=True)
class Fold:
    """What folding one tree writes and deletes; ``baseline`` is ``None`` when the tree needed no fold."""

    tree: str
    baseline: Path | None
    deleted: tuple[Path, ...]


def floor_revision(repo: Path, floor_tag: str, tree: str) -> str:
    """The head revision ``floor_tag`` shipped for ``tree``, read from the contract document at that tag."""
    document = f"contracts/store/{tree}.json"
    text = Repository(repo).show(floor_tag, document)
    if text is None:
        raise FoldError(f"{floor_tag} has no {document}, so it cannot be a fold floor for the {tree} tree")
    head = json.loads(text)["head_revision"]
    if not isinstance(head, str):
        raise FoldError(f"{floor_tag}'s {document} names no head revision")
    return head


def fold_tree(repo: Path, floor_tag: str, tree: str, render_floor: Renderer) -> Fold:
    """Replace ``tree``'s floor revision with a baseline and delete everything it absorbed, tests included.

    ``ABSORBED`` carries every id the baseline stands for that the tree no longer holds: the ids deleted now, and
    the previous baseline's own id and ``ABSORBED`` when the tree was folded before. It never holds the baseline's
    own id, which stays the floor revision's, so the revisions above it keep their ``down_revision``."""
    floor = floor_revision(repo, floor_tag, tree)
    migrations = repo / _MIGRATIONS.format(tree=tree)
    revisions = read_tree(migrations / "versions")
    if floor not in revisions:
        raise FoldError(f"{floor_tag} ships {tree} revision {floor}, which this tree does not hold")
    base = base_of(revisions)
    if base.revision == floor:
        return Fold(tree, None, ())
    below = ancestors(revisions, floor)
    absorbed = frozenset(entry.revision for entry in below) | base.absorbed
    source = write_baseline(
        tree=tree, revision=floor, floor_tag=floor_tag, absorbed=absorbed, rendered=render_floor(tree)
    )
    ast.parse(source)
    doomed = [entry.path for entry in below]
    tests = migrations / "tests"
    colocated = [tests / f"{entry.path.stem}_test.py" for entry in (*below, revisions[floor])]
    deleted = (*doomed, *(path for path in colocated if path.exists()))
    revisions[floor].path.write_text(source)
    for path in deleted:
        path.unlink()
    return Fold(tree, revisions[floor].path, deleted)


def fold_store(repo: Path, floor_tag: str, render_floor: Renderer | None = None) -> list[Fold]:
    """Fold every tree up to ``floor_tag``; with no ``render_floor``, the release's own wheel renders them.

    Every tree is checked before any is written, so a refusal on one leaves none half-folded."""
    for tree in TREES:
        floor_revision(repo, floor_tag, tree)
    if render_floor is not None:
        return [fold_tree(repo, floor_tag, tree, render_floor) for tree in TREES]
    with tempfile.TemporaryDirectory(prefix="store-fold-") as scratch:
        work = Path(scratch)
        floor = install(floor_tag, download_release(floor_tag, work / "release"), work / "venv")
        return [fold_tree(repo, floor_tag, tree, floor_renderer(floor, work)) for tree in TREES]


def floor_renderer(floor: Installation, work: Path) -> Renderer:
    """The renderer that builds a tree's history with ``floor``'s own ``migrate`` and renders it with its Python."""

    def render_floor(tree: str) -> Mapping[str, Any]:
        directory = work / f"{tree}-history"
        return render(floor, tree, history_store(floor, tree, directory))

    return render_floor
