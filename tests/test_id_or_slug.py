"""The shared id-or-slug resolution and slug grammar, over a kind of its own rather than any stored one (unit tier)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import NewType

import pytest

from blizzard.foundation.ids import Id, IdPrefix
from blizzard.hub.domain.kernel.id_or_slug import Match, MatchedBy, SluggedKind, locate, resolve
from blizzard.hub.domain.kernel.slugs import DisplayName, Slug

pytestmark = pytest.mark.unit

_AT = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)

_ThingId = NewType("_ThingId", str)
_KIND = SluggedKind(IdPrefix.CHUNK, _ThingId, Slug.of)


@dataclass(frozen=True)
class _Thing:
    thing_id: _ThingId
    slug: str


@dataclass
class _Things:
    """An in-memory tenant's records and former slugs, as a store answers them."""

    current: dict[str, _Thing] = field(default_factory=dict)
    former: dict[str, _Thing] = field(default_factory=dict)

    def add(self, slug: str) -> _Thing:
        thing = _Thing(_ThingId(Id.mint_at(IdPrefix.CHUNK, _AT).value), slug)
        self.current[slug] = thing
        return thing

    def move(self, thing: _Thing, to: str) -> _Thing:
        """``thing`` takes ``to``; the slug it leaves becomes former, and ``to`` is no longer former."""
        moved = _Thing(thing.thing_id, to)
        del self.current[thing.slug]
        self.former.pop(to, None)
        self.former[thing.slug] = moved
        self.current[to] = moved
        return moved

    def get(self, record_id: _ThingId, /) -> _Thing | None:
        return next((t for t in self.current.values() if t.thing_id == record_id), None)

    def get_by_slug(self, slug: Slug, /) -> _Thing | None:
        return self.current.get(slug.value)

    def get_by_former_slug(self, slug: Slug, /) -> _Thing | None:
        return self.former.get(slug.value)


@pytest.mark.parametrize("raw", ["", "Winter", "a_b", "-a", "a-", "a--b", " a", "a" * 65, "a\n"])
def test_a_malformed_slug_is_none(raw: str) -> None:
    assert Slug.of(raw) is None


@pytest.mark.parametrize("raw", ["a", "a-b", "winter2", "a" * 64])
def test_a_well_formed_slug_parses(raw: str) -> None:
    assert Slug.of(raw) == Slug(raw)


@pytest.mark.parametrize("raw", ["", " a", "a ", "a" * 129])
def test_a_malformed_display_name_is_none(raw: str) -> None:
    assert DisplayName.of(raw) is None


def test_an_id_resolves_by_id_whatever_the_slug() -> None:
    things = _Things()
    moved = things.move(things.add("winter"), "frost")
    assert locate(moved.thing_id, _KIND, things) == Match(moved, MatchedBy.ID)


def test_an_id_never_resolves_as_a_slug() -> None:
    things = _Things()
    held = things.add("winter")
    unknown = Id.mint_at(IdPrefix.CHUNK, _AT).value
    things.current[unknown] = held
    assert resolve(unknown, _KIND, things) is None


def test_another_kinds_id_is_not_an_id() -> None:
    assert not _KIND.is_id(Id.mint_at(IdPrefix.GRAPH, _AT).value)
    assert _KIND.is_id(Id.mint_at(IdPrefix.CHUNK, _AT).value)


def test_a_current_slug_resolves_before_a_former_one() -> None:
    things = _Things()
    left = things.move(things.add("winter"), "frost")
    things.former["frost"] = left
    assert locate("frost", _KIND, things) == Match(left, MatchedBy.SLUG)


def test_a_former_slug_resolves_to_its_holder_until_it_is_reclaimed() -> None:
    things = _Things()
    winter = things.move(things.add("winter"), "frost")
    other = things.add("other")
    assert locate("winter", _KIND, things) == Match(winter, MatchedBy.FORMER_SLUG)
    other = things.move(other, "winter")
    assert locate("winter", _KIND, things) == Match(other, MatchedBy.SLUG)
    assert locate("other", _KIND, things) == Match(other, MatchedBy.FORMER_SLUG)


@pytest.mark.parametrize("value", ["Winter", "winter framework", " winter", "ch_short"])
def test_a_value_outside_the_slug_grammar_resolves_to_nothing(value: str) -> None:
    things = _Things()
    things.add("winter")
    assert resolve(value, _KIND, things) is None
