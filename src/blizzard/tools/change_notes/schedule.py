"""Release-line ordering and the current removal schedule of released deprecations."""

from __future__ import annotations

from collections.abc import Iterable

from blizzard.tools.change_notes.contract import Kind, Note
from blizzard.tools.change_notes.tree import UNRELEASED


def release_line(version: str) -> str:
    """The removal-line vocabulary for a release version, independent of its patch."""
    major, minor, _patch = (int(part) for part in version.split("."))
    return f"0.{minor}" if major == 0 else str(major)


def line_order(line: str) -> tuple[int, int]:
    """Compare pre-1.0 minor lines and post-1.0 major lines in one ordering."""
    parts = tuple(int(part) for part in line.split("."))
    return (parts[0], parts[1] if len(parts) == 2 else 0)


def latest_released_schedule(notes: Iterable[Note]) -> dict[str, str]:
    """Each released deprecation's line after its latest released postponement."""
    released = sorted(
        (note for note in notes if note.directory != UNRELEASED),
        key=lambda note: (tuple(int(part) for part in note.directory.split(".")), note.id),
    )
    schedule = {
        note.id: line
        for note in released
        if note.fields.get("kind") == Kind.DEPRECATED
        if isinstance(line := note.fields.get("removes-in"), str)
    }
    for note in released:
        target = note.fields.get("postpones")
        line = note.fields.get("removes-in")
        if isinstance(target, str) and target in schedule and isinstance(line, str):
            schedule[target] = line
    return schedule


def unretired_due(notes: Iterable[Note], version: str) -> tuple[str, ...]:
    """Released deprecations due at the cut without a removal on this line or a postponement in this cut."""
    all_notes = list(notes)
    current = release_line(version)
    postponed = {n.fields.get("postpones") for n in all_notes if n.directory == UNRELEASED}
    retired = {
        n.fields.get("retires")
        for n in all_notes
        if n.directory == UNRELEASED or (n.directory != UNRELEASED and release_line(n.directory) == current)
    }
    return tuple(
        sorted(
            note_id
            for note_id, line in latest_released_schedule(all_notes).items()
            if line_order(line) <= line_order(current) and note_id not in postponed and note_id not in retired
        )
    )
