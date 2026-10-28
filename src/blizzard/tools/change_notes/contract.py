"""The change-note contract: vocabulary, the parsed ``Note``, and the per-note rules.

Every refusal is a ``Failure`` carrying the note's path and a stable kebab ``rule`` token; the
rules here judge one note alone, and ``validate`` adds the ones that span the tree.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from blizzard.foundation.roles import domain_model
from blizzard.foundation.untyped import as_list

ID_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ID_MAX_LENGTH = 60
REMOVES_IN_PATTERN = re.compile(r"^([1-9][0-9]*|0\.[1-9][0-9]*)$")
UPGRADE_HEADING = "Upgrade"
FLAG_REGISTRY = "src/blizzard/foundation/flags/registry.toml"


class Kind(StrEnum):
    ADDED = "added"
    CHANGED = "changed"
    DEPRECATED = "deprecated"
    REMOVED = "removed"
    FIXED = "fixed"
    SECURITY = "security"


class Surface(StrEnum):
    HTTP_API = "http-api"
    WIRE = "wire"
    CONFIG = "config"
    STORE = "store"
    TRACE = "trace"
    EGRESS = "egress"
    CLI = "cli"
    BOARD = "board"


@domain_model
@dataclass(frozen=True)
class Failure:
    """One refusal: the file it names and the rule it broke."""

    path: str
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}: {self.rule}: {self.detail}"


@domain_model
@dataclass(frozen=True)
class Note:
    """A parsed change note: its repo-relative path, directory, id, frontmatter and body."""

    path: str
    directory: str
    id: str
    fields: Mapping[str, Any]
    body: str


type FieldRule = Callable[[Any, Mapping[str, Any]], list[tuple[str, str]]]


def _is_string_list(value: object) -> bool:
    items = as_list(value)
    return items is not None and all(isinstance(item, str) for item in items)


def _kind(value: Any, _fields: Mapping[str, Any]) -> list[tuple[str, str]]:
    if value in tuple(Kind):
        return []
    return [("bad-kind", f"kind must be one of {', '.join(k.value for k in Kind)}, not {value!r}")]


def _surfaces(value: Any, _fields: Mapping[str, Any]) -> list[tuple[str, str]]:
    known = {s.value for s in Surface}
    if not _is_string_list(value) or not set(value) <= known or len(set(value)) != len(value):
        return [("bad-surfaces", f"surfaces must be distinct values from {', '.join(sorted(known))}")]
    return []


def _breaking(value: Any, fields: Mapping[str, Any]) -> list[tuple[str, str]]:
    if not isinstance(value, bool):
        return [("bad-breaking", "breaking must be true or false")]
    if value and fields.get("kind") not in (Kind.CHANGED, Kind.REMOVED):
        return [("breaking-kind", "breaking: true is only for kind changed or removed")]
    return []


def _breaks(value: Any, fields: Mapping[str, Any]) -> list[tuple[str, str]]:
    if not _is_string_list(value):
        return [("bad-breaks", "breaks must be a list of strings")]
    if fields.get("breaking") is not True:
        return [("breaks-not-breaking", "breaks is only for a note with breaking: true")]
    return []


def _removes_in(value: Any, fields: Mapping[str, Any]) -> list[tuple[str, str]]:
    if fields.get("kind") != Kind.DEPRECATED and "postpones" not in fields:
        return [("removes-in-misplaced", "removes-in is only for kind deprecated or a note that postpones")]
    if not isinstance(value, str) or not REMOVES_IN_PATTERN.match(value):
        return [("bad-removes-in", 'removes-in must be a quoted major ("2") or pre-1.0 minor ("0.12")')]
    return []


def _postpones(value: Any, fields: Mapping[str, Any]) -> list[tuple[str, str]]:
    if fields.get("kind") != Kind.CHANGED:
        return [("postpones-kind", "postpones is only for kind changed")]
    return _note_id(value, "postpones")


def _retires(value: Any, fields: Mapping[str, Any]) -> list[tuple[str, str]]:
    if fields.get("kind") != Kind.REMOVED:
        return [("retires-kind", "retires is only for kind removed")]
    return _note_id(value, "retires")


def _note_id(value: Any, field: str) -> list[tuple[str, str]]:
    if not isinstance(value, str) or not ID_PATTERN.match(value):
        return [(f"bad-{field}", f"{field} must name a note id")]
    return []


def _upgrade_guide(value: Any, _fields: Mapping[str, Any]) -> list[tuple[str, str]]:
    if not isinstance(value, str) or not value or value.startswith("#"):
        return [("bad-upgrade-guide", "upgrade-guide must be an anchor name without the leading '#'")]
    return []


def _flag(value: Any, _fields: Mapping[str, Any]) -> list[tuple[str, str]]:
    if not isinstance(value, str):
        return [("bad-flag", "flag must name a declared flag")]
    return []


FIELD_RULES: Mapping[str, FieldRule] = {
    "kind": _kind,
    "surfaces": _surfaces,
    "breaking": _breaking,
    "breaks": _breaks,
    "removes-in": _removes_in,
    "postpones": _postpones,
    "retires": _retires,
    "upgrade-guide": _upgrade_guide,
    "flag": _flag,
}

_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def headings(text: str) -> list[tuple[int, str, int]]:
    """Every ATX heading outside fenced code, as ``(level, text, line index)``."""
    found: list[tuple[int, str, int]] = []
    fence: str | None = None
    for index, line in enumerate(text.splitlines()):
        opening = _FENCE.match(line)
        if opening:
            marker = opening.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        heading = _HEADING.match(line)
        if heading:
            found.append((len(heading.group(1)), (heading.group(2) or "").strip(), index))
    return found


def _body_failures(note: Note) -> list[tuple[str, str]]:
    if not note.body.strip():
        return [("empty-body", "the body must not be empty")]
    lines = note.body.splitlines()
    found = headings(note.body)
    failures: list[tuple[str, str]] = []
    upgrades = [h for h in found if h[:2] == (2, UPGRADE_HEADING)]
    for level, text, _ in found:
        if (level, text) != (2, UPGRADE_HEADING):
            failures.append(("bad-heading", f"the only heading allowed is '## {UPGRADE_HEADING}', not {text!r}"))
    if len(upgrades) > 1:
        failures.append(("duplicate-upgrade", f"'## {UPGRADE_HEADING}' appears more than once"))
    first = found[0][2] if found else len(lines)
    if not "\n".join(lines[:first]).strip():
        failures.append(("empty-body", "the prose before the first heading must not be empty"))
    if upgrades and not "\n".join(lines[upgrades[0][2] + 1 :]).strip():
        failures.append(("empty-upgrade", f"'## {UPGRADE_HEADING}' must not be empty"))
    needs_upgrade = note.fields.get("breaking") is True or note.fields.get("kind") == Kind.REMOVED
    if needs_upgrade and not upgrades:
        failures.append(("upgrade-required", f"a breaking or removed note needs a '## {UPGRADE_HEADING}' section"))
    return failures


def check_note(note: Note) -> list[Failure]:
    """Every rule that judges this note alone: id, frontmatter fields, body."""
    problems: list[tuple[str, str]] = []
    if not ID_PATTERN.match(note.id):
        problems.append(("bad-id", f"the id {note.id!r} must be lowercase words joined by single hyphens"))
    if len(note.id) > ID_MAX_LENGTH:
        problems.append(("id-too-long", f"the id is {len(note.id)} characters; the limit is {ID_MAX_LENGTH}"))
    if "kind" not in note.fields:
        problems.append(("missing-kind", "kind is required"))
    for name, value in note.fields.items():
        rule = FIELD_RULES.get(name)
        if rule is None:
            problems.append(("unknown-field", f"{name!r} is not a change-note field"))
        else:
            problems.extend(rule(value, note.fields))
    needs_removes_in = note.fields.get("kind") == Kind.DEPRECATED or "postpones" in note.fields
    if needs_removes_in and "removes-in" not in note.fields:
        problems.append(("removes-in-required", "removes-in is required on kind deprecated and with postpones"))
    problems.extend(_body_failures(note))
    return [Failure(note.path, rule, detail) for rule, detail in problems]
