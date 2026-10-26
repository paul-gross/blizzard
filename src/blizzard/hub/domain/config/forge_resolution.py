"""Which forge a repository or work-source write names — the ``forge`` reference, the old address fields, or neither.

Through the forge transition every door still takes the old address fields — ``forge_api_url`` on a repository,
``api_base`` and ``web_base`` on a work source — and reads ``forge`` first. :func:`decide_forge` is the one rule: it
answers the forge a write names, or the origin no forge holds yet, which the caller finds or creates, and every old
field the write used, each of which the write is warned about. What an omitted field means differs between creating a
record and editing one: a work source created with no address is on GitHub, while an edit that names nothing keeps the
forge the record already names."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.changes import ChangeContext, ConfigChange
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forges import (
    GITHUB_API_BASE,
    ConfiguredForge,
    ForgeId,
    ForgeKindMismatch,
    ForgeUnavailable,
    canonical_field,
    forge_for_origin,
)
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType

#: The reference field every write names its forge by.
FORGE_FIELD = "forge"


class ForgeConflict(ConfigFieldError):
    """An old field disagrees with the forge the write names."""

    code = "forge_conflict"


class ForgeRequired(ConfigFieldError):
    """A write names no forge where one is required."""

    code = "forge_required"


@domain_model
@dataclass(frozen=True)
class ForgeChoice:
    """What a write says about its forge: ``forge`` names one by id or slug; ``api_base`` and ``web_base`` are the old
    address fields, :data:`UNSET` when the write left them out and ``None`` when it sent them null."""

    forge: str | UnsetType = UNSET
    api_base: str | None | UnsetType = UNSET
    web_base: str | None | UnsetType = UNSET


@domain_model
@dataclass(frozen=True)
class ForgeRules:
    """How one record kind's old fields read: the wire name its API address arrives under, whether it carries a web
    address, whether a missing API address means GitHub's, and the forge kind it must name."""

    api_field: str
    web_field: str | None
    defaults_to_github: bool

    def kind_for(self, provider: str | None) -> str:
        """The forge kind a record of ``provider`` names — ``github`` for a repository, which has none."""
        return provider or "github"


#: A repository's old field, ``forge_api_url``, is required on a create that names no forge.
REPOSITORY_FORGE_RULES = ForgeRules(api_field="forge_api_url", web_field=None, defaults_to_github=False)
#: A work source's old fields; a work source given no address is on GitHub.
WORK_SOURCE_FORGE_RULES = ForgeRules(api_field="api_base", web_field="web_base", defaults_to_github=True)


@domain_model
@dataclass(frozen=True)
class OldFieldUsed:
    """One old address field a write used, and the forge it resolved to."""

    field: str
    forge_id: ForgeId
    forge_slug: str
    message: str


@domain_model
@dataclass(frozen=True)
class ForgeWanted:
    """An API origin no forge of the tenant holds, which the write's forge is made for — with the web origin the write
    declared for it, if any."""

    api_base: str
    web_base: str | None
    kind: str


@domain_model
@dataclass(frozen=True)
class ForgeDecision:
    """The forge a write names — ``forge``, or ``wanted`` when none holds its origin yet — and the old fields it
    used."""

    forge: ConfiguredForge | None
    wanted: ForgeWanted | None
    old_fields: tuple[str, ...] = ()

    def warnings(self, forge: ConfiguredForge) -> tuple[OldFieldUsed, ...]:
        """One warning per old field used, naming ``forge``, the one the write resolved to."""
        return tuple(
            OldFieldUsed(
                field=field,
                forge_id=forge.forge_id,
                forge_slug=forge.slug,
                message=f"{field} is deprecated; it resolved to forge {forge.slug} — name it as `forge` instead",
            )
            for field in self.old_fields
        )


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def decide_forge(
    choice: ForgeChoice,
    rules: ForgeRules,
    *,
    provider: str | None,
    current: ConfiguredForge | None,
    named: ConfiguredForge | None,
    holder_of: Callable[[str], ConfiguredForge | None],
) -> ForgeDecision | None:
    """The forge ``choice`` names for a record of ``provider``. ``current`` is the forge an edited record names —
    ``None`` on a create; ``named`` is what ``choice.forge`` resolved to; ``holder_of`` answers the forge, retired or
    not, holding a canonical API origin. ``None`` when an edit names nothing, leaving the forge as stored.

    :class:`ForgeUnavailable` for an unknown or retired forge, or an origin a retired forge holds;
    :class:`ForgeConflict` for an old field that disagrees with the named forge; :class:`ForgeRequired` for a
    repository created naming none; :class:`ForgeKindMismatch` for a forge of another kind than ``provider``; and
    :class:`ConfigFieldError` on the field whose address is not an origin."""
    kind = rules.kind_for(provider)
    old: list[str] = []
    forge: ConfiguredForge | None = None
    wanted: ForgeWanted | None = None
    if choice.forge is not UNSET:
        if named is None or named.retired:
            state = "retired" if named is not None else "unknown"
            raise ForgeUnavailable(FORGE_FIELD, f"forge {choice.forge} is {state}")
        forge = named
        if choice.api_base is not UNSET:
            old.append(rules.api_field)
            origin = _api_origin(choice.api_base, rules)
            if origin != forge.fields.api_base:
                raise ForgeConflict(
                    rules.api_field, f"{origin} is not the API origin of forge {forge.label}; send `forge` alone"
                )
    elif choice.api_base is not UNSET or current is None:
        if choice.api_base is UNSET and not rules.defaults_to_github:
            raise ForgeRequired(FORGE_FIELD, f"name the forge as `{FORGE_FIELD}`")
        old.append(rules.api_field)
        origin = _api_origin(None if choice.api_base is UNSET else choice.api_base, rules)
        forge = holder_of(origin)
        if forge is None:
            wanted = ForgeWanted(origin, None, kind)
        elif forge.retired:
            raise ForgeUnavailable(
                rules.api_field, f"{origin} is held by retired forge {forge.slug}; enable that forge instead"
            )
    elif choice.web_base is UNSET:
        return None
    else:
        forge = current
    if rules.web_field is not None and choice.web_base is not UNSET:
        old.append(rules.web_field)
        if not _blank(choice.web_base):
            assert choice.web_base is not None
            web = canonical_field(rules.web_field, choice.web_base)
            if forge is not None and web != forge.web_origin:
                raise ForgeConflict(
                    rules.web_field,
                    f"{web} is not the web origin of forge {forge.slug}, {forge.web_origin}; "
                    "set the forge's web_base, or leave this field out",
                )
            if wanted is not None:
                wanted = ForgeWanted(wanted.api_base, web, kind)
    if forge is not None and forge.kind != kind:
        raise ForgeKindMismatch(FORGE_FIELD, f"forge {forge.slug} is a {forge.kind} forge, not {kind}")
    return ForgeDecision(forge, wanted, tuple(old))


def _api_origin(value: str | None, rules: ForgeRules) -> str:
    """The canonical API origin an old field names; a blank one is GitHub's where the kind defaults to it."""
    if _blank(value):
        if not rules.defaults_to_github:
            raise ConfigFieldError(rules.api_field, "must not be blank" if value is not None else "must not be null")
        return GITHUB_API_BASE
    assert value is not None
    return canonical_field(rules.api_field, value)


@domain_model
@dataclass(frozen=True)
class ResolvedForge:
    """The forge a write resolved to, and a warning for each old field it used."""

    forge: ConfiguredForge
    warnings: tuple[OldFieldUsed, ...] = ()


@domain_model
@dataclass(frozen=True)
class Authored[T]:
    """A record a write left, with a warning for each old field the write used."""

    record: T
    warnings: tuple[OldFieldUsed, ...] = ()


class ForgePlanner:
    """Resolves many writes' forges against the tenant's forges at once, as a document apply or a legacy import does:
    one forge is planned per API origin no forge holds, however many entries name it and however each spells it.
    ``resolve`` answers a ``forge`` reference; ``forges`` are every forge of the tenant, retired ones included."""

    def __init__(
        self,
        forges: Iterable[ConfiguredForge],
        ctx: ChangeContext,
        *,
        at: datetime,
        resolve: Callable[[str], ConfiguredForge | None],
    ) -> None:
        self._by_id: dict[str, ConfiguredForge] = {}
        self._by_origin: dict[str, ConfiguredForge] = {}
        for forge in forges:
            self._hold(forge)
        self._ctx = ctx
        self._at = at
        self._resolve = resolve
        #: Each forge planned, with its change and the entry that first named it.
        self.planned: list[tuple[ConfiguredForge, ConfigChange, int]] = []

    def _hold(self, forge: ConfiguredForge) -> None:
        self._by_id[forge.forge_id] = forge
        self._by_origin[forge.fields.api_base] = forge

    def forge(self, forge_id: str) -> ConfiguredForge | None:
        """The forge, held or planned, with id ``forge_id``."""
        return self._by_id.get(forge_id)

    def decide(
        self,
        choice: ForgeChoice,
        rules: ForgeRules,
        *,
        provider: str | None,
        current: ForgeId | None,
        index: int,
    ) -> ResolvedForge | None:
        """:func:`decide_forge` for one entry — at ``index`` of its section — planning the forge it names when none
        holds its origin; ``None`` when an edit names nothing."""
        named = None
        if choice.forge is not UNSET:
            named = self._by_id.get(choice.forge) or self._resolve(choice.forge)
        decision = decide_forge(
            choice,
            rules,
            provider=provider,
            current=None if current is None else self._by_id.get(current),
            named=named,
            holder_of=self._by_origin.get,
        )
        if decision is None:
            return None
        forge = decision.forge
        if forge is None:
            assert decision.wanted is not None
            wanted = decision.wanted
            taken = {held.slug for held in self._by_id.values()}
            forge, change = forge_for_origin(
                wanted.api_base, wanted.web_base, taken, self._ctx, at=self._at, kind=wanted.kind
            )
            self._hold(forge)
            self.planned.append((forge, change, index))
        return ResolvedForge(forge, decision.warnings(forge))
