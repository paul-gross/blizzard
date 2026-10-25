"""The cut: move the unreleased notes, and the upgrade boundaries they name, under a release's version."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.roles import domain_model
from blizzard.tools.change_notes.anchors import anchor_titles
from blizzard.tools.change_notes.breaks import BreakEntry, cross_check
from blizzard.tools.change_notes.flags import working_registry
from blizzard.tools.change_notes.schedule import cut_refusals
from blizzard.tools.change_notes.tree import CHANGES_DIR, UNRELEASED, VERSION_DIR, read_tree
from blizzard.tools.change_notes.upgrade_doc import UpgradeDocError, move_boundaries, staged_anchors
from blizzard.tools.change_notes.validate import UPGRADE_DOC, validate
from blizzard.tools.compat.reader import CompatError


class CutRefused(RuntimeError):
    """The cut would not leave the tree sound; nothing was written."""


@domain_model
@dataclass(frozen=True)
class CutReport:
    """What a cut moved: note ids, boundary anchors, and named anchors left where they were."""

    version: str
    notes: tuple[str, ...]
    boundaries: tuple[str, ...]
    left: tuple[str, ...]
    held: tuple[str, ...]
    unrecorded: tuple[tuple[str, str], ...] = ()


def cut(root: Path, version: str, breaks: Callable[[], Sequence[BreakEntry]] | None = None) -> CutReport:
    """Move ``changes/unreleased/`` into ``changes/<version>/``; refuse before writing anything.

    ``breaks`` yields the breaks compat lets through; each must be named by a note the version will hold."""
    if not VERSION_DIR.match(version):
        raise CutRefused(f"{version!r} is not a release version; expected X.Y.Z")
    failures = validate(root)
    if failures:
        raise CutRefused("\n".join([*map(str, failures), f"{len(failures)} change-note failure(s)"]))
    notes, _ = read_tree(root)
    unreleased = sorted((n for n in notes if n.directory == UNRELEASED), key=lambda n: n.id)
    defaults = working_registry(root)
    moving = [n for n in unreleased if not n.fields.get("flag") or defaults[n.fields["flag"]]["default"] == "on"]
    held = tuple(n.id for n in unreleased if n not in moving)
    refusals = cut_refusals(notes, version, moving)
    if refusals:
        raise CutRefused("\n".join(refusals))
    unrecorded: tuple[tuple[str, str], ...] = ()
    if breaks is not None:
        try:
            recorded = breaks()
        except CompatError as exc:
            raise CutRefused(str(exc)) from exc
        prospective = [n for n in notes if n.directory == version] + moving
        check = cross_check(recorded, prospective)
        if check.unnamed:
            raise CutRefused(
                "\n".join(
                    [
                        *(
                            f"no breaking note names {entry}: list its surface in surfaces and its where in breaks"
                            for entry in check.unnamed
                        ),
                        f"{len(check.unnamed)} compat break(s) unnamed",
                    ]
                )
            )
        unrecorded = check.unrecorded
    guides = sorted({g for n in moving if isinstance(g := n.fields.get("upgrade-guide"), str)})
    doc = root / UPGRADE_DOC
    original = doc.read_text(encoding="utf-8") if guides else ""
    try:
        moved = move_boundaries(original, version, guides)
    except UpgradeDocError as exc:
        raise CutRefused(str(exc)) from exc
    held_guides = {g for n in unreleased if n.id in held if isinstance(g := n.fields.get("upgrade-guide"), str)}
    displaced = held_guides & (staged_anchors(original) - staged_anchors(moved.text))
    if displaced:
        raise CutRefused(f"held notes' upgrade boundaries would move with another note: {', '.join(sorted(displaced))}")
    if guides:
        before, after = anchor_titles(original), anchor_titles(moved.text)
        for note in notes:
            guide = note.fields.get("upgrade-guide")
            if isinstance(guide, str) and after.get(guide) != before.get(guide):
                raise CutRefused(
                    f"{note.path}: the rewrite of {UPGRADE_DOC} leaves anchor {guide!r} on another heading"
                )
    if moved.text != original:
        doc.write_text(moved.text, encoding="utf-8")
    target = root / CHANGES_DIR / version
    for note in moving:
        target.mkdir(exist_ok=True)
        (root / note.path).rename(target / f"{note.id}.md")
    left = tuple(g for g in guides if g not in moved.anchors)
    return CutReport(version, tuple(n.id for n in moving), moved.anchors, left, held, unrecorded)
