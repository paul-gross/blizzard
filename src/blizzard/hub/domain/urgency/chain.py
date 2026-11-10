"""The urgency chain: the urgency a chunk is minted with, taken from the first link that names one — the urgency the
mint states, then the chore's override, then the routine's default, then the tenant's default.

The chain is pure: each link arrives as the urgency it names, read under the mint's locks, and the function decides
over them alone. A stated urgency that is retired is refused; a chore's or routine's that is retired names none, so
the chain falls through to the next link."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.urgency.model import Urgency, require_live
from blizzard.hub.domain.urgency.settings import DEFAULT_URGENCY_FIELD

#: The field a mint request states its urgency under.
URGENCY_FIELD = "urgency"


@domain_model
@dataclass(frozen=True)
class ChainLinks:
    """What each link above the tenant's default names — ``None`` for a link naming none, or one the mint has
    none of."""

    stated: Urgency | None = None
    chore: Urgency | None = None
    routine: Urgency | None = None


def resolve_chain(links: ChainLinks, default: Urgency) -> Urgency:
    """The urgency the chunk is minted with. :class:`~blizzard.hub.domain.urgency.model.UrgencyRetired` when the
    stated urgency, or the default the chain falls through to, is retired."""
    if links.stated is not None:
        return require_live(URGENCY_FIELD, links.stated.slug, links.stated)
    for link in (links.chore, links.routine):
        if link is not None and not link.retired:
            return link
    return require_live(DEFAULT_URGENCY_FIELD, default.slug, default)
