"""The cut: move the unreleased notes, and the upgrade boundaries they name, under a release's version."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.roles import domain_model
from blizzard.tools.change_notes.anchors import anchor_titles
from blizzard.tools.change_notes.flags import working_registry
from blizzard.tools.change_notes.contract import Note
from blizzard.tools.change_notes.schedule import latest_released_schedule, line_order, release_line
from blizzard.tools.change_notes.tree import CHANGES_DIR, UNRELEASED, VERSION_DIR, read_tree
from blizzard.tools.change_notes.upgrade_doc import UpgradeDocError, move_boundaries, staged_anchors
from blizzard.tools.change_notes.validate import UPGRADE_DOC, validate


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


def _schedule_refusals(notes: list[Note], moving: list[Note], version: str) -> list[str]:
    current = release_line(version)
    scheduled = latest_released_schedule(notes)
    refusals: list[str] = []
    for note in moving:
        line = note.fields.get("removes-in")
        if not isinstance(line, str):
            continue
        expected = "pre-1.0 minor" if version.startswith("0.") else "major"
        if ("." in line) != (expected == "pre-1.0 minor"):
            refusals.append(f"{note.path}: removes-in must name a {expected} line at {version}")
        elif line_order(line) <= line_order(current):
            refusals.append(f"{note.path}: removes-in {line} must be later than cut line {current}")
        target = note.fields.get("postpones")
        if isinstance(target, str) and target in scheduled and line_order(line) <= line_order(scheduled[target]):
            refusals.append(
                f"{note.path}: postpones {target} to {line}, "
                f"not later than its latest released line {scheduled[target]}"
            )
    major, minor, patch = (int(part) for part in version.split("."))
    if patch == 0 and (major != 0 or minor != 0):
        postponed = {n.fields.get("postpones") for n in moving}
        retirements = [
            (n.fields.get("retires"), line_order(current if n.directory == UNRELEASED else release_line(n.directory)))
            for n in notes
            if n.directory == UNRELEASED or line_order(release_line(n.directory)) <= line_order(current)
        ]
        for note_id, due in scheduled.items():
            retired = any(rid == note_id and rline >= line_order(due) for rid, rline in retirements)
            if line_order(due) <= line_order(current) and not retired and note_id not in postponed:
                refusals.append(f"deprecation {note_id} is due in {current} without a retiring removal or postponement")
    return refusals


def cut(root: Path, version: str) -> CutReport:
    """Move ``changes/unreleased/`` into ``changes/<version>/``; refuse before writing anything."""
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
    refusals = _schedule_refusals(notes, moving, version)
    if refusals:
        raise CutRefused("\n".join(refusals))
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
    return CutReport(version, tuple(n.id for n in moving), moved.anchors, left, held)
