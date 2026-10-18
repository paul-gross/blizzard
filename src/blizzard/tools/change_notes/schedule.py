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


def due_without_removal(
    notes: Iterable[Note], line: str, *, prospective: bool = False, through_version: str | None = None
) -> tuple[str, ...]:
    """Promises due by a line, excluding only retirements already carried or prospectively cut."""
    all_notes = list(notes)
    schedule = latest_released_schedule(all_notes)
    limit = tuple(int(part) for part in through_version.split(".")) if through_version else None
    retirements = [
        (n.fields.get("retires"), line_order(release_line(n.directory)))
        for n in all_notes
        if n.directory != UNRELEASED
        and line_order(release_line(n.directory)) <= line_order(line)
        and (limit is None or tuple(int(part) for part in n.directory.split(".")) <= limit)
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


def cut_refusals(notes: Iterable[Note], version: str, moving: Iterable[Note] | None = None) -> list[str]:
    """The schedule promises a prospective cut must keep before it moves any files."""
    limit = tuple(int(part) for part in version.split("."))
    material = list(notes)
    selected = list(moving) if moving is not None else [n for n in material if n.directory == UNRELEASED]
    all_notes = [
        n
        for n in material
        if n.directory != UNRELEASED and tuple(int(part) for part in n.directory.split(".")) <= limit
    ] + selected
    current = release_line(version)
    scheduled = latest_released_schedule(all_notes)
    refusals: list[str] = []
    for note in selected:
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
        postponed = {n.fields.get("postpones") for n in selected}
        for note_id in due_without_removal(all_notes, current, prospective=True, through_version=version):
            if note_id not in postponed:
                refusals.append(f"deprecation {note_id} is due in {current} without a retiring removal or postponement")
    return refusals
