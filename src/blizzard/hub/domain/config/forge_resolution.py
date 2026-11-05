"""Which forge a repository or work-source write names — its ``forge`` reference, by id or slug.

:func:`resolve_forge` is the one rule every door and the apply share. A record is created on the forge it names, and an
edit that names none keeps the forge the record names. The address fields a record once carried — ``forge_api_url`` on
a repository, ``api_base`` and ``web_base`` on a work source — belong to the forge now: a write that sends one is
refused :class:`FieldMovedToForge`, naming the forge that carries it where one can be told."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forges import (
    ConfiguredForge,
    ForgeKindMismatch,
    ForgeUnavailable,
    OriginRefused,
    canonical_origin,
)
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType

#: The reference field every write names its forge by.
FORGE_FIELD = "forge"

#: The address fields the forge carries, each with whether it is the forge's API origin (``True``) or its web origin.
MOVED_FIELDS: dict[str, bool] = {"forge_api_url": True, "api_base": True, "web_base": False}


class ForgeRequired(ConfigFieldError):
    """A write names no forge where one is required."""

    code = "forge_required"


class FieldMovedToForge(ConfigFieldError):
    """A write sent an address field the forge now carries."""

    code = "field_moved_to_forge"


@domain_model
@dataclass(frozen=True)
class MovedField:
    """An address field a write sent — one of :data:`MOVED_FIELDS` — with the value it sent, ``None`` when that was no
    string."""

    field: str
    value: str | None


@domain_model
@dataclass(frozen=True)
class ForgeChoice:
    """What a write says about its forge: ``forge`` names one by id or slug, :data:`UNSET` when the write left it out,
    and ``named`` is the forge the edge resolved that reference to — ``None`` when it names none; ``moved`` is every
    address field the write sent beside it."""

    forge: str | UnsetType = UNSET
    named: ConfiguredForge | None = None
    moved: tuple[MovedField, ...] = ()


def resolve_forge(
    choice: ForgeChoice,
    *,
    provider: str | None,
    current: ConfiguredForge | None,
    creating: bool,
    forges: Callable[[], Iterable[ConfiguredForge]],
) -> ConfiguredForge | None:
    """The forge ``choice`` names for a record of ``provider`` — a repository has none and names a ``github`` forge.
    ``current`` is the forge an edited record names; ``forges`` answers every forge of the tenant, retired included,
    read only to name the forge a moved field belongs to. ``None`` when an edit names no forge, leaving it as stored.

    :class:`FieldMovedToForge` for an address field sent; :class:`ForgeRequired` for a create naming no forge;
    :class:`ForgeUnavailable` for an unknown or retired forge; :class:`ForgeKindMismatch` for a forge of another kind
    than ``provider``."""
    if choice.moved:
        raise _moved(choice, current, forges)
    if choice.forge is UNSET:
        if creating:
            raise ForgeRequired(FORGE_FIELD, f"name the forge as `{FORGE_FIELD}`")
        return None
    named = choice.named
    if named is None or named.retired:
        state = "retired" if named is not None else "unknown"
        raise ForgeUnavailable(FORGE_FIELD, f"forge {choice.forge} is {state}")
    kind = provider or "github"
    if named.kind != kind:
        raise ForgeKindMismatch(FORGE_FIELD, f"forge {named.slug} is a {named.kind} forge, not {kind}")
    return named


def _moved(
    choice: ForgeChoice, current: ConfiguredForge | None, forges: Callable[[], Iterable[ConfiguredForge]]
) -> FieldMovedToForge:
    """The refusal of the first moved field ``choice`` sent, naming — in order — the forge the write names, the forge
    the edited record names, or the tenant's forge holding the origin a moved field sent."""
    first = choice.moved[0]
    forge = choice.named if choice.forge is not UNSET else None
    forge = forge or current or _holder(choice.moved, forges())
    if forge is None:
        return FieldMovedToForge(first.field, f"{first.field} belongs to the forge now; name it as `{FORGE_FIELD}`")
    return FieldMovedToForge(
        first.field, f"{first.field} belongs to forge {forge.slug} now; name that forge as `{FORGE_FIELD}`"
    )


def _holder(moved: tuple[MovedField, ...], forges: Iterable[ConfiguredForge]) -> ConfiguredForge | None:
    """The forge holding an origin a moved field sent, compared canonical: the API origin for an API field, the
    effective web origin for the web field."""
    held = tuple(forges)
    for sent in moved:
        if sent.value is None:
            continue
        try:
            origin = canonical_origin(sent.value)
        except OriginRefused:
            continue
        api = MOVED_FIELDS[sent.field]
        for forge in held:
            if origin == (forge.fields.api_base if api else forge.web_origin):
                return forge
    return None
