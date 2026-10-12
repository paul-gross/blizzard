"""Resolving a reference — an id or a slug — to the record it names, for every slugged kind alike.

A value carrying the kind's id prefix resolves as an id whatever its slug; otherwise a current slug, then a slug a
record left. A value outside the slug grammar — a display name among them — matches nothing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.kernel.slugs import Slug


class MatchedBy(StrEnum):
    """Which rule a reference resolved by."""

    ID = "id"
    SLUG = "slug"
    FORMER_SLUG = "former_slug"


@domain_model
@dataclass(frozen=True)
class SluggedKind[K: str, S: Slug]:
    """What resolution needs of a kind: its id prefix, its id type, and its slug type."""

    prefix: IdPrefix
    id_of: Callable[[str], K]
    slug_of: Callable[[str], S | None]

    def is_id(self, value: str) -> bool:
        """Whether ``value`` has this kind's ``<prefix>_<ulid>`` form, which always resolves as an id."""
        parsed = Id.parse(value)
        return parsed is not None and parsed.has_prefix(self.prefix)


class IIdOrSlugLookup[K: str, S: Slug, R](Protocol):
    """The reads a resolution needs; a kind's read seam is one."""

    def get(self, record_id: K, /) -> R | None: ...

    def get_by_slug(self, slug: S, /) -> R | None: ...

    def get_by_former_slug(self, slug: S, /) -> R | None: ...


@domain_model
@dataclass(frozen=True)
class Match[R]:
    """The record a reference names, and whether it named it by id, current slug, or a slug it left."""

    record: R
    by: MatchedBy


def locate[K: str, S: Slug, R](
    value: str, kind: SluggedKind[K, S], records: IIdOrSlugLookup[K, S, R]
) -> Match[R] | None:
    """The record ``value`` names, and how, by the module's rules."""
    if kind.is_id(value):
        found = records.get(kind.id_of(value))
        return None if found is None else Match(found, MatchedBy.ID)
    slug = kind.slug_of(value)
    if slug is None:
        return None
    current = records.get_by_slug(slug)
    if current is not None:
        return Match(current, MatchedBy.SLUG)
    former = records.get_by_former_slug(slug)
    return None if former is None else Match(former, MatchedBy.FORMER_SLUG)


def resolve[K: str, S: Slug, R](value: str, kind: SluggedKind[K, S], records: IIdOrSlugLookup[K, S, R]) -> R | None:
    """The record ``value`` names (:func:`locate`), or ``None`` when it names none."""
    match = locate(value, kind, records)
    return None if match is None else match.record
