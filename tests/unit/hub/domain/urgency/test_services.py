"""Resolving urgency keys against one read of the tenant's urgencies: by id, by slug, and never by a slug left."""

from __future__ import annotations

from datetime import UTC, datetime

from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.kernel import id_or_slug
from blizzard.hub.domain.urgency.model import URGENCY_KIND, Urgency, UrgencyShape
from blizzard.hub.domain.urgency.services import HeldUrgencies

_AT = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
_CTX = ChangeContext(actor="usr_op", door=Door.API)


def _new(slug: str) -> Urgency:
    return Urgency.new(UrgencyShape(slug=slug), _CTX, siblings=lambda: (), pick=lambda colors: colors[0], at=_AT)[0]


def test_the_set_lookup_resolves_an_id_and_a_slug_and_misses_the_rest() -> None:
    urgent, standard = _new("urgent"), _new("standard")
    held = HeldUrgencies.of([urgent, standard])

    assert id_or_slug.resolve(urgent.urgency_id, URGENCY_KIND, held) == urgent
    assert id_or_slug.resolve("standard", URGENCY_KIND, held) == standard
    assert id_or_slug.resolve("later", URGENCY_KIND, held) is None
    assert id_or_slug.resolve("urg_01M74CHKQJS9D51CS3KA16713B", URGENCY_KIND, held) is None
    assert id_or_slug.locate("urgent", URGENCY_KIND, held) == id_or_slug.Match(urgent, id_or_slug.MatchedBy.SLUG)


def test_the_set_lookup_matches_no_slug_an_urgency_left() -> None:
    held = HeldUrgencies.of([_new("urgent")])
    slug = URGENCY_KIND.slug_of("urgent")
    assert slug is not None

    assert held.get_by_former_slug(slug) is None
