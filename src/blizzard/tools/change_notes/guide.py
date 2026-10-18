"""Assemble a line's upgrade guide from released notes and the prospective cut."""

from __future__ import annotations

from collections.abc import Iterable

from blizzard.tools.change_notes.contract import Kind, Note
from blizzard.tools.change_notes.render import UPGRADE_DOC_URL, _split
from blizzard.tools.change_notes.schedule import latest_released_schedule, line_order, release_line
from blizzard.tools.change_notes.tree import UNRELEASED


def _version(note: Note) -> tuple[int, int, int, str]:
    major, minor, patch = (int(part) for part in note.directory.split("."))
    return major, minor, patch, note.id


def _entry(note: Note, tag: str | None, *, level: int = 3) -> str:
    prose, upgrade = _split(note)
    guide = note.fields.get("upgrade-guide")
    link = (
        (UPGRADE_DOC_URL.format(tag=tag, anchor=guide) if tag else f"docs/upgrade.md#{guide}")
        if isinstance(guide, str)
        else None
    )
    parts = [f"{'#' * level} {prose.splitlines()[0]}", "", prose]
    if upgrade:
        parts.extend(["", f"{'#' * (level + 1)} Upgrade", "", upgrade])
    if link:
        parts.extend(["", f"[Detailed upgrade instructions]({link})"])
    return "\n".join(parts)


def due_without_removal(notes: Iterable[Note], line: str, *, prospective: bool = False) -> tuple[str, ...]:
    """Deprecations due on this line without a removal in this line's notes."""
    all_notes = list(notes)
    material = [n for n in all_notes if n.directory != UNRELEASED]
    schedule = latest_released_schedule(material)
    retirements = [
        (n.fields.get("retires"), line_order(release_line(n.directory)))
        for n in material
        if line_order(release_line(n.directory)) <= line_order(line)
    ]
    if prospective:
        retirements.extend((n.fields.get("retires"), line_order(line)) for n in all_notes if n.directory == UNRELEASED)
    return tuple(
        sorted(
            note_id
            for note_id, due in schedule.items()
            if line_order(due) <= line_order(line)
            and not any(
                retired_id == note_id and retired_line >= line_order(due) for retired_id, retired_line in retirements
            )
        )
    )


def guide(notes: Iterable[Note], line: str, *, tag: str | None = None, prospective: bool = False) -> str:
    """Render deprecations due on a line, their removals, then unpaired breaking changes."""
    all_notes = list(notes)
    material = [n for n in all_notes if n.directory != UNRELEASED]
    members = [n for n in all_notes if n.directory == UNRELEASED] if prospective else []
    schedule = latest_released_schedule(material)
    deprecations = sorted(
        (n for n in material if n.fields.get("kind") == Kind.DEPRECATED and schedule.get(n.id) == line),
        key=_version,
    )
    removals = {
        n.fields["retires"]: n
        for n in members + sorted([n for n in material if release_line(n.directory) == line], key=_version)
        if isinstance(n.fields.get("retires"), str)
    }
    paired: set[str] = set()
    sections: list[str] = []
    for note in deprecations:
        sections.append(_entry(note, tag))
        if removal := removals.get(note.id):
            sections.append(_entry(removal, tag, level=4))
            paired.add(removal.id)
    sections.extend(
        _entry(n, tag)
        for n in sorted([n for n in material if release_line(n.directory) == line] + members, key=lambda n: n.id)
        if n.id not in paired and (n.fields.get("kind") == Kind.REMOVED or n.fields.get("breaking") is True)
    )
    return f"## Upgrading to {line}\n\n" + "\n\n".join(sections) + "\n" if sections else ""
