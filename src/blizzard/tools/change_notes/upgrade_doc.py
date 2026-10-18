"""``docs/upgrade.md`` boundaries moved between release headings — pure over the document's text."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.tools.change_notes.anchors import anchored_headings

STAGING_HEADING = "Upgrading to the next release"
BEFORE_HEADING = "Before you pull"


class UpgradeDocError(ValueError):
    """The document cannot take the move."""


@domain_model
@dataclass(frozen=True)
class Moved:
    """The rewritten document and the anchors of the boundaries that moved, in staging order."""

    text: str
    anchors: tuple[str, ...]


def _section_end(heads: list[tuple[str, int, int]], position: int, total: int) -> int:
    level = heads[position][1]
    return next((index for _, lvl, index in heads[position + 1 :] if lvl <= level), total)


def _trimmed(lines: list[str]) -> list[str]:
    end = len(lines)
    while end and not lines[end - 1].strip():
        end -= 1
    return lines[:end]


def _release_heading(version: str) -> str:
    return f"Upgrading to {version}"


def move_boundaries(text: str, version: str, wanted: Collection[str]) -> Moved:
    """Move each staged boundary whose anchor is in ``wanted`` under ``## Upgrading to <version>``.

    A boundary is a section under the staging heading. It lands at the end of an existing release section, or in
    a new one directly beneath ``## Before you pull``. Without a staging heading, or with nothing wanted staged,
    the text is returned as it came."""
    lines = text.splitlines()
    heads = heads_before = anchored_headings(text)
    staged = next(
        (i for i, (_, lvl, idx) in enumerate(heads) if lvl == 2 and _title(lines[idx]) == STAGING_HEADING), None
    )
    if staged is None:
        return Moved(text, ())
    stage_end = _section_end(heads, staged, len(lines))
    ranges: list[tuple[str, int, int]] = []
    for position in range(staged + 1, len(heads)):
        anchor, _, start = heads[position]
        if start >= stage_end:
            break
        if anchor in wanted and not (ranges and start < ranges[-1][2]):
            ranges.append((anchor, start, _section_end(heads, position, len(lines))))
    if not ranges:
        return Moved(text, ())
    blocks = [_trimmed(lines[start:end]) for _, start, end in ranges]
    kept = [line for index, line in enumerate(lines) if not any(s <= index < e for _, s, e in ranges)]
    remaining = "\n".join(kept)
    heads = anchored_headings(remaining)
    target = next(
        (i for i, (_, lvl, idx) in enumerate(heads) if lvl == 2 and _title(kept[idx]) == _release_heading(version)),
        None,
    )
    if target is not None:
        end = _section_end(heads, target, len(kept))
        at = len(_trimmed(kept[:end]))
        insert = [line for block in blocks for line in ["", *block]]
    else:
        before = next(
            (i for i, (_, lvl, idx) in enumerate(heads) if lvl == 2 and _title(kept[idx]) == BEFORE_HEADING), None
        )
        if before is None:
            raise UpgradeDocError(
                f"docs/upgrade.md has no '## {BEFORE_HEADING}' heading to place '## {_release_heading(version)}' after"
            )
        at = len(_trimmed(kept[: _section_end(heads, before, len(kept))]))
        insert = ["", f"## {_release_heading(version)}", *[line for block in blocks for line in ["", *block]]]
    rewritten = [*kept[:at], *insert, *kept[at:]]
    trailing = "\n" if text.endswith("\n") else ""
    nested = tuple(a for a, _, i in heads_before if a in wanted and any(s <= i < e for _, s, e in ranges))
    return Moved("\n".join(rewritten) + trailing, nested)


def _title(line: str) -> str:
    return line.strip().lstrip("#").strip()
