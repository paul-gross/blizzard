"""The urgency chain: the urgency a chunk is minted with, taken from the first link that names one — the urgency the
mint states, then the chore's override, then the routine's default, then the tenant's default.

The chain is pure: each link arrives as the urgency it names, read under the mint's locks, and the function decides
over them alone. A stated urgency that is retired is refused; a chore's or routine's that is retired names none, so
the chain falls through to the next link."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.urgency.model import Urgency, require_live
from blizzard.hub.domain.urgency.settings import DEFAULT_URGENCY_FIELD

#: The field a mint request states its urgency under.
URGENCY_FIELD = "urgency"


def required_urgency_reference(reference: str | None) -> str:
    """``reference`` — the urgency an edit names for a chunk: a chunk always carries one, so a ``null`` is no clear
    and is refused."""
    if reference is None:
        raise ConfigFieldError(URGENCY_FIELD, "must not be null")
    return reference


@domain_model
@dataclass(frozen=True)
class ChainLinks[T]:
    """What each link above the tenant's default names — by id ahead of the mint's unit, as the urgency read under
    its locks — ``None`` for a link naming none, or one the mint has none of."""

    stated: T | None = None
    chore: T | None = None
    routine: T | None = None

    def named(self) -> list[T]:
        return [link for link in (self.stated, self.chore, self.routine) if link is not None]


def resolve_chain(links: ChainLinks[Urgency], default: Urgency) -> Urgency:
    """The urgency the chunk is minted with. :class:`~blizzard.hub.domain.urgency.model.UrgencyRetired` when the
    stated urgency, or the default the chain falls through to, is retired."""
    if links.stated is not None:
        return require_live(URGENCY_FIELD, links.stated.slug, links.stated)
    for link in (links.chore, links.routine):
        if link is not None and not link.retired:
            return link
    return require_live(DEFAULT_URGENCY_FIELD, default.slug, default)
