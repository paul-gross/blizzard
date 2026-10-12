"""The grammars every slugged record kind shares: a slug that resolves, and a display name that never does.

A kind's own refusals — a reserved slug, a field error naming its document key — stay the kind's; only the grammar is
shared (``bzh:configured-record``)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Self

from blizzard.foundation.roles import domain_model

_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SLUG_MAX = 64
NAME_MAX = 128


_RUN_OUTSIDE_ALPHABET = re.compile(r"[^a-z0-9]+")


def derive_slug(raw: str, *, fallback: str) -> str:
    """The slug closest to ``raw``: lowercased, each run outside ``[a-z0-9]`` collapsed to one hyphen, trimmed of
    hyphens and cut to :data:`SLUG_MAX`; ``fallback`` when nothing is left."""
    derived = _RUN_OUTSIDE_ALPHABET.sub("-", raw.lower()).strip("-")[:SLUG_MAX].strip("-")
    return derived or fallback


@domain_model
@dataclass(frozen=True)
class Slug:
    """A well-formed slug: 1-64 lowercase letters, digits and single inner hyphens."""

    value: str

    @classmethod
    def of(cls, raw: str) -> Self | None:
        """The slug ``raw`` spells, or ``None`` when it is outside the slug alphabet or length."""
        if len(raw) > SLUG_MAX or not _SLUG_PATTERN.match(raw):
            return None
        return cls(raw)


@domain_model
@dataclass(frozen=True)
class DisplayName:
    """A display name: 1-128 characters with no leading or trailing whitespace. Nothing resolves a name."""

    value: str

    @classmethod
    def of(cls, raw: str) -> Self | None:
        """The name ``raw`` spells, or ``None`` when it is blank, too long, or padded."""
        if not raw or len(raw) > NAME_MAX or raw != raw.strip():
            return None
        return cls(raw)
