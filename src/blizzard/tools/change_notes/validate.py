"""Whole-tree validation: per-note rules plus id uniqueness and the references between notes."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from blizzard.tools.change_notes.anchors import anchors
from blizzard.tools.change_notes.contract import Failure, Kind, Note, check_note
from blizzard.tools.change_notes.tree import UNRELEASED, read_tree

UPGRADE_DOC = "docs/upgrade.md"


def _duplicate_ids(notes: list[Note]) -> list[Failure]:
    by_id: dict[str, list[Note]] = defaultdict(list)
    for note in notes:
        by_id[note.id].append(note)
    return [
        Failure(note.path, "duplicate-id", f"the id is also used by {other.path}")
        for group in by_id.values()
        if len(group) > 1
        for note in group
        for other in group
        if other is not note
    ]


def _unresolved_references(notes: list[Note]) -> list[Failure]:
    released_deprecations = {
        note.id for note in notes if note.directory != UNRELEASED and note.fields.get("kind") == Kind.DEPRECATED
    }
    return [
        Failure(
            note.path,
            f"unresolved-{field}",
            f"{field} names {target!r}, which is no deprecated note under a released directory",
        )
        for note in notes
        for field in ("retires", "postpones")
        if isinstance(target := note.fields.get(field), str) and target not in released_deprecations
    ]


def _unknown_anchors(root: Path, notes: list[Note]) -> list[Failure]:
    guided = [n for n in notes if isinstance(n.fields.get("upgrade-guide"), str)]
    if not guided:
        return []
    doc = root / UPGRADE_DOC
    if not doc.is_file():
        return [
            Failure(n.path, "missing-upgrade-doc", f"upgrade-guide needs {UPGRADE_DOC}, which does not exist")
            for n in guided
        ]
    known = anchors(doc.read_text(encoding="utf-8"))
    return [
        Failure(n.path, "unknown-anchor", f"{UPGRADE_DOC} has no heading anchor {n.fields['upgrade-guide']!r}")
        for n in guided
        if n.fields["upgrade-guide"] not in known
    ]


def validate(root: Path) -> list[Failure]:
    """Every failure in ``root/changes``, ordered by path then rule."""
    notes, failures = read_tree(root)
    for note in notes:
        failures.extend(check_note(note))
    failures.extend(_duplicate_ids(notes))
    failures.extend(_unresolved_references(notes))
    failures.extend(_unknown_anchors(root, notes))
    return sorted(failures, key=lambda f: (f.path, f.rule))
