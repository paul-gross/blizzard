"""blizzard:unit-test for the breaks cross-check: which recorded breaks a version's notes name."""

from __future__ import annotations

from typing import TypedDict, Unpack

import pytest

from blizzard.tools.change_notes.breaks import BreakEntry, cross_check
from blizzard.tools.change_notes.contract import Note

pytestmark = pytest.mark.unit


class _NoteFields(TypedDict, total=False):
    breaking: bool
    surfaces: list[str]
    breaks: list[str]


def _note(note_id: str, **fields: Unpack[_NoteFields]) -> Note:
    return Note(path=f"changes/0.3.0/{note_id}.md", directory="0.3.0", id=note_id, fields=fields, body="Body.\n")


_ROUTE = BreakEntry("v0.2.0", "http-api", "GET /old")


def test_a_breaking_note_naming_the_surface_and_where_names_the_entry() -> None:
    note = _note("a", breaking=True, surfaces=["http-api"], breaks=["GET /old", "GET /ghost"])
    check = cross_check([_ROUTE], [note])
    assert [(n.entry, n.note.id) for n in check.named] == [(_ROUTE, "a")]
    assert check.unnamed == () and check.unrecorded == (("http-api", "GET /ghost"),)


@pytest.mark.parametrize(
    "fields",
    [
        {"breaking": False, "surfaces": ["http-api"], "breaks": ["GET /old"]},
        {"breaking": True, "surfaces": ["cli"], "breaks": ["GET /old"]},
        {"breaking": True, "surfaces": ["http-api"], "breaks": ["GET /other"]},
    ],
)
def test_a_non_breaking_note_a_wrong_surface_or_a_wrong_name_leaves_the_entry_unnamed(fields: _NoteFields) -> None:
    check = cross_check([_ROUTE], [_note("a", **fields)])
    assert check.named == () and check.unnamed == (_ROUTE,)
