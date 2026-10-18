"""The anchors GitHub renders for the headings of a Markdown text."""

from __future__ import annotations

import re

from blizzard.tools.change_notes.contract import headings

_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_DROPPED = re.compile(r"[^\w\- ]", re.UNICODE)


def _slug(text: str) -> str:
    plain = _LINK.sub(r"\1", text).replace("`", "").replace("*", "")
    return _DROPPED.sub("", plain.strip().lower()).replace(" ", "-")


def anchors(markdown: str) -> set[str]:
    """Every heading's slug, a repeat taking GitHub's ``-1``, ``-2`` suffixes. ATX headings only."""
    seen: dict[str, int] = {}
    found: set[str] = set()
    for _, text, _ in headings(markdown):
        slug = _slug(text)
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        found.add(slug if count == 0 else f"{slug}-{count}")
    return found
