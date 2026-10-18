"""The anchors GitHub renders for the headings of a Markdown text."""

from __future__ import annotations

import re

from blizzard.tools.change_notes.contract import headings

_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_DROPPED = re.compile(r"[^\w\- ]", re.UNICODE)


def _slug(text: str) -> str:
    plain = _LINK.sub(r"\1", text).replace("`", "").replace("*", "")
    return _DROPPED.sub("", plain.strip().lower()).replace(" ", "-")


def anchored_headings(markdown: str) -> list[tuple[str, int, int]]:
    """Every ATX heading as ``(anchor, level, line index)``, a repeat taking GitHub's ``-1``, ``-2`` suffixes."""
    seen: dict[str, int] = {}
    found: list[tuple[str, int, int]] = []
    for level, text, index in headings(markdown):
        slug = _slug(text)
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        found.append((slug if count == 0 else f"{slug}-{count}", level, index))
    return found


def anchors(markdown: str) -> set[str]:
    """Every heading's anchor. ATX headings only."""
    return {anchor for anchor, _, _ in anchored_headings(markdown)}


def anchor_titles(markdown: str) -> dict[str, tuple[int, str]]:
    """Each heading anchor mapped to the level and text of the heading it names."""
    return {
        anchor: (level, text)
        for (anchor, level, _), (_, text, _) in zip(anchored_headings(markdown), headings(markdown), strict=True)
    }
