"""The breaks cross-check: which of ``blizzard-compat``'s recorded breaks a release's notes name."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.roles import domain_model
from blizzard.tools.change_notes.contract import Note
from blizzard.tools.compat.reader import BREAKS_FILE, tag_breaks


class BreaksFileInvalid(RuntimeError):
    """``compat-breaks.json`` is missing, unreadable, or not the document ``blizzard-compat`` writes."""


@domain_model
@dataclass(frozen=True)
class BreakEntry:
    """One break ``blizzard-compat`` lets through: the release it breaks, its surface, and where."""

    release: str
    surface: str
    where: str

    def __str__(self) -> str:
        return f"{self.surface} {self.where} (breaks {self.release})"


@domain_model
@dataclass(frozen=True)
class NamedBreak:
    """A recorded break and the note that names it."""

    entry: BreakEntry
    note: Note


@domain_model
@dataclass(frozen=True)
class BreaksCheck:
    """A cross-check's outcome: breaks named, breaks no note names, and declared pairs no break records."""

    named: tuple[NamedBreak, ...]
    unnamed: tuple[BreakEntry, ...]
    unrecorded: tuple[tuple[str, str], ...]


def declared_pairs(note: Note) -> set[tuple[str, str]]:
    """The ``(surface, where)`` a breaking note declares: each of its surfaces against each name in ``breaks``."""
    if note.fields.get("breaking") is not True:
        return set()
    surfaces, names = note.fields.get("surfaces") or [], note.fields.get("breaks") or []
    return {(str(surface), str(name).strip()) for surface in surfaces for name in names}


def parse_breaks(text: str) -> tuple[str, tuple[BreakEntry, ...]]:
    """The tag a ``compat-breaks.json`` judged and its entries, one per element."""
    try:
        document = json.loads(text)
        return document["tag"], tuple(
            BreakEntry(item["release"], item["surface"], item["where"]) for item in document["breaks"]
        )
    except (ValueError, KeyError, TypeError) as error:
        raise BreaksFileInvalid(f"{BREAKS_FILE} is unreadable: {error!r}") from error


def read_breaks(path: Path) -> tuple[str, tuple[BreakEntry, ...]]:
    """``parse_breaks`` over the file at ``path``."""
    try:
        return parse_breaks(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise BreaksFileInvalid(f"{path} could not be read: {error}") from error


def compat_breaks(root: Path, version: str) -> tuple[BreakEntry, ...]:
    """The breaks ``blizzard-compat --tag <version>`` lets through at ``root``."""
    return parse_breaks(tag_breaks(root, version))[1]


def cross_check(entries: Sequence[BreakEntry], notes: Sequence[Note]) -> BreaksCheck:
    """Match each entry to the note, by id, whose declared (surface, where) is the entry's."""
    ordered = sorted(notes, key=lambda n: n.id)
    declared = {note.id: declared_pairs(note) for note in ordered}
    named: list[NamedBreak] = []
    unnamed: list[BreakEntry] = []
    for entry in entries:
        owner = next((n for n in ordered if (entry.surface, entry.where) in declared[n.id]), None)
        if owner is None:
            unnamed.append(entry)
        else:
            named.append(NamedBreak(entry, owner))
    recorded = {(e.surface, e.where) for e in entries}
    unrecorded = sorted({pair for pairs in declared.values() for pair in pairs} - recorded)
    return BreaksCheck(tuple(named), tuple(unnamed), tuple(unrecorded))


def unnamed_lines(check: BreaksCheck, hint: str = "") -> str:
    """The refusal text for a check's unnamed entries: one line each, then the count; ``hint`` ends each line."""
    return "\n".join(
        [
            *(f"no breaking note names {entry}{hint}" for entry in check.unnamed),
            f"{len(check.unnamed)} compat break(s) unnamed",
        ]
    )
