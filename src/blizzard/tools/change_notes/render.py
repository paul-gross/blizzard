"""A release's notes as Markdown — pure over the notes, the also-in map, and the commit block's text."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from blizzard.tools.change_notes.breaks import NamedBreak
from blizzard.tools.change_notes.contract import UPGRADE_HEADING, Kind, Note, headings

UPGRADE_DOC_URL = "https://github.com/paul-gross/blizzard/blob/{tag}/docs/upgrade.md#{anchor}"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")
_KIND_SECTIONS = ((Kind.ADDED, "Added"), (Kind.CHANGED, "Changed"), (Kind.FIXED, "Fixed"), (Kind.SECURITY, "Security"))


def split(note: Note) -> tuple[str, str]:
    """The body before its ``## Upgrade`` section, and that section's text (empty when there is none)."""
    lines = note.body.splitlines()
    start = next((i for lvl, text, i in headings(note.body) if (lvl, text) == (2, UPGRADE_HEADING)), None)
    if start is None:
        return note.body.strip(), ""
    return "\n".join(lines[:start]).strip(), "\n".join(lines[start + 1 :]).strip()


def _item(text: str, marker: str, trailer: str = "") -> str:
    lines = text.splitlines()
    if marker:
        lines[-1] += f" {marker}"
    lines.extend([trailer] if trailer else [])
    return "\n".join(("- " if i == 0 else "  ") + line if line else "" for i, line in enumerate(lines))


def _section(title: str, parts: Sequence[str], *, separator: str = "\n") -> str:
    return f"## {title}\n\n" + separator.join(parts) + "\n" if parts else ""


def _section_of(note: Note) -> str:
    kind = note.fields["kind"]
    if note.fields.get("breaking") is True:
        return "Breaking changes"
    if kind == Kind.DEPRECATED:
        return "Deprecated"
    if kind == Kind.REMOVED:
        return "Changed"
    return next(title for k, title in _KIND_SECTIONS if k == kind)


def _upgrade_entry(note: Note, tag: str, prose: str, upgrade: str) -> str:
    return f"### {_title(note, tag, prose)}\n\n{upgrade}"


def first_sentence(prose: str) -> str:
    """The first sentence of prose, with whitespace normalized to one line."""
    return _SENTENCE_END.split(" ".join(prose.split()), maxsplit=1)[0]


def _title(note: Note, tag: str, prose: str) -> str:
    sentence = first_sentence(prose)
    guide = note.fields.get("upgrade-guide")
    return f"[{sentence}]({UPGRADE_DOC_URL.format(tag=tag, anchor=guide)})" if isinstance(guide, str) else sentence


def _break_entry(named: NamedBreak, tag: str) -> str:
    entry = named.entry
    title = _title(named.note, tag, split(named.note)[0])
    return f"### {title}\n\nBreaks {entry.where} ({entry.surface}) in {entry.release}."


def render(
    tag: str,
    notes: Sequence[Note],
    also_in: Mapping[str, Sequence[str]],
    commits: str,
    turned_on: Sequence[str] = (),
    schedule: Mapping[str, str] | None = None,
    breaks: Sequence[NamedBreak] = (),
) -> str:
    """The published notes for ``tag``: sections in order, empty ones omitted, then the collapsed commits."""
    grouped: dict[str, list[str]] = {}
    upgrades: list[str] = []
    for note in sorted(notes, key=lambda n: n.id):
        prose, upgrade = split(note)
        versions = also_in.get(note.id, ())
        marker = f"(also in {', '.join(versions)})" if versions else ""
        line = (schedule or {}).get(note.id, note.fields.get("removes-in"))
        trailer = f"Removal planned for {line}." if note.fields["kind"] == Kind.DEPRECATED else ""
        grouped.setdefault(_section_of(note), []).append(_item(prose, marker, trailer))
        if upgrade:
            upgrades.append(_upgrade_entry(note, tag, prose, upgrade))
    upgrades.extend(_break_entry(named, tag) for named in breaks)
    order = ("Breaking changes", "Deprecated", *(title for _, title in _KIND_SECTIONS))
    sections = [
        _section("Upgrade notes", upgrades, separator="\n\n"),
        _section("Turned on", [f"- {summary}" for summary in turned_on]),
        *(_section(title, grouped.get(title, [])) for title in order),
        f"## Commits\n\n<details>\n<summary>Commits</summary>\n\n{commits.strip()}\n\n</details>\n",
    ]
    return "\n".join(s for s in sections if s)
