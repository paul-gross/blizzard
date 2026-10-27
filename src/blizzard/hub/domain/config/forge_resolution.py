"""Which forge a repository or work-source write names — the ``forge`` reference, the old address fields, or neither.

Through the forge transition every door still takes the old address fields — ``forge_api_url`` on a repository,
``api_base`` and ``web_base`` on a work source — and reads ``forge`` first. :func:`decide_forge` is the one rule: it
answers the forge a write names, or the origin no forge holds yet, which :class:`ForgePlanner` plans a forge for, and
every old field the write used, each of which the write is warned about and records a ``config-old-field-used`` event
for (:func:`record_old_fields`). What an omitted field means differs between creating a
record and editing one: a work source created with no address is on GitHub, while an edit that names nothing keeps the
forge the record already names."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.apply import PlannedForge
from blizzard.hub.domain.config.changes import ChangeContext
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
    """What a write says about its forge: ``forge`` names one by id or slug, and ``named`` is the forge the edge
    resolved that reference to — ``None`` when it names none; ``api_base`` and ``web_base`` are the old address fields,
    :data:`UNSET` when the write left them out and ``None`` when it sent them null."""

    forge: str | UnsetType = UNSET
    api_base: str | None | UnsetType = UNSET
    web_base: str | None | UnsetType = UNSET
    named: ConfiguredForge | None = None


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


#: The event each old field a write used records.
OLD_FIELD_USED: EventLogKind = "config-old-field-used"


class IEventRecorder(Protocol):
    """Records one operational event, naming no runner, chunk, project, lease, or node."""

    def record(
        self,
        *,
        kind: EventLogKind,
        runner_id: None,
        chunk_id: None,
        project_id: None,
        lease_id: None,
        node_name: None,
        message: str,
        detail: dict[str, str] | None,
        at: datetime,
    ) -> int: ...


def record_old_fields(events: IEventRecorder, used: Sequence[OldFieldUsed], *, record: str, at: datetime) -> None:
    """One ``config-old-field-used`` event per old field in ``used``, which the write naming ``record`` leaned on."""
    for warning in used:
        events.record(
            kind=OLD_FIELD_USED,
            runner_id=None,
            chunk_id=None,
            project_id=None,
            lease_id=None,
            node_name=None,
            message=f"{record}: {warning.message}",
            detail={
                "record": record,
                "field": warning.field,
                "forge_id": warning.forge_id,
                "forge_slug": warning.forge_slug,
            },
            at=at,
        )


def _old_field_warnings(old_fields: tuple[str, ...], forge: ConfiguredForge) -> tuple[OldFieldUsed, ...]:
    return tuple(
        OldFieldUsed(
            field=field,
            forge_id=forge.forge_id,
            forge_slug=forge.slug,
            message=f"{field} is deprecated; it resolved to forge {forge.slug} — name it as `forge` instead",
        )
        for field in old_fields
    )


@domain_model
@dataclass(frozen=True)
class ForgeFound:
    """A write names ``forge``, a forge the tenant holds, having used ``old_fields`` to say so."""

    forge: ConfiguredForge
    old_fields: tuple[str, ...] = ()

    def warnings(self) -> tuple[OldFieldUsed, ...]:
        """One warning per old field used, naming the forge."""
        return _old_field_warnings(self.old_fields, self.forge)


@domain_model
@dataclass(frozen=True)
class ForgeWanted:
    """A write names an API origin no forge of the tenant holds, which its forge is made for — with the web origin the
    write declared for it, if any — having used ``old_fields`` to say so."""

    api_base: str
    web_base: str | None
    kind: str
    old_fields: tuple[str, ...] = ()

    def warnings(self, made: ConfiguredForge) -> tuple[OldFieldUsed, ...]:
        """One warning per old field used, naming ``made``, the forge the origin resolved to."""
        return _old_field_warnings(self.old_fields, made)


#: The forge a write names: one the tenant holds, or an origin none holds yet.
type ForgeDecision = ForgeFound | ForgeWanted


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def decide_forge(
    choice: ForgeChoice,
    rules: ForgeRules,
    *,
    provider: str | None,
    current: ConfiguredForge | None,
    holder_of: Callable[[str], ConfiguredForge | None],
) -> ForgeDecision | None:
    """The forge ``choice`` names for a record of ``provider``. ``current`` is the forge an edited record names —
    ``None`` on a create; ``holder_of`` answers the forge, retired or not, holding a canonical API origin. ``None`` when
    an edit names nothing, leaving the forge as stored.

    :class:`ForgeUnavailable` for an unknown or retired forge, or an origin a retired forge holds;
    :class:`ForgeConflict` for an old field that disagrees with the named forge; :class:`ForgeRequired` for a
    repository created naming none; :class:`ForgeKindMismatch` for a forge of another kind than ``provider``; and
    :class:`ConfigFieldError` on the field whose address is not an origin."""
    kind = rules.kind_for(provider)
    old: list[str] = []
    forge: ConfiguredForge | None
    origin: str | None = None
    if choice.forge is not UNSET:
        named = choice.named
        if named is None or named.retired:
            state = "retired" if named is not None else "unknown"
            raise ForgeUnavailable(FORGE_FIELD, f"forge {choice.forge} is {state}")
        forge = named
        if choice.api_base is not UNSET:
            old.append(rules.api_field)
            stated = _api_origin(choice.api_base, rules)
            if stated != forge.fields.api_base:
                raise ForgeConflict(
                    rules.api_field, f"{stated} is not the API origin of forge {forge.label}; send `forge` alone"
                )
    elif choice.api_base is not UNSET or current is None:
        if choice.api_base is UNSET and not rules.defaults_to_github:
            raise ForgeRequired(FORGE_FIELD, f"name the forge as `{FORGE_FIELD}`")
        old.append(rules.api_field)
        origin = _api_origin(None if choice.api_base is UNSET else choice.api_base, rules)
        forge = holder_of(origin)
        if forge is not None and forge.retired:
            raise ForgeUnavailable(
                rules.api_field, f"{origin} is held by retired forge {forge.slug}; enable that forge instead"
            )
    elif choice.web_base is UNSET:
        return None
    else:
        forge = current
    web = _web_origin(choice, rules)
    if rules.web_field is not None and choice.web_base is not UNSET:
        old.append(rules.web_field)
    if forge is None:
        assert origin is not None
        return ForgeWanted(origin, web, kind, tuple(old))
    if web is not None and web != forge.web_origin:
        raise _web_conflict(rules, web, f"of forge {forge.slug}", forge.web_origin)
    if forge.kind != kind:
        raise ForgeKindMismatch(FORGE_FIELD, f"forge {forge.slug} is a {forge.kind} forge, not {kind}")
    return ForgeFound(forge, tuple(old))


def _web_origin(choice: ForgeChoice, rules: ForgeRules) -> str | None:
    """The canonical web origin ``choice`` states — ``None`` when it states none, or a blank one, which means the
    forge's."""
    if rules.web_field is None or choice.web_base is UNSET or _blank(choice.web_base):
        return None
    assert choice.web_base is not None
    return canonical_field(rules.web_field, choice.web_base)


def _web_conflict(rules: ForgeRules, web: str, whose: str, held: str) -> ForgeConflict:
    """A ``web_base`` other than ``held``, the web origin ``whose`` names."""
    assert rules.web_field is not None
    return ForgeConflict(
        rules.web_field,
        f"{web} is not the web origin {whose}, {held}; set the forge's web_base, or leave this field out",
    )


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
    """Resolves writes' forges against the tenant's forges, as a single write, a document apply, and a legacy import
    each do: one forge is planned per API origin no forge holds, however many entries name it and however each spells
    it. ``forges`` are every forge of the tenant, retired ones included.

    The web origin a planned forge takes is the first explicit one an entry declares for its API origin, whatever entry
    first names that origin, so an entry naming the origin without a web origin never refuses one declaring it later
    (:meth:`foresee`)."""

    def __init__(self, forges: Iterable[ConfiguredForge], ctx: ChangeContext, *, at: datetime) -> None:
        self._by_id: dict[str, ConfiguredForge] = {}
        self._by_origin: dict[str, ConfiguredForge] = {}
        for forge in forges:
            self._hold(forge)
        self._ctx = ctx
        self._at = at
        self._webs: dict[str, str] = {}
        #: Each forge planned, attributed to the entry that first named it.
        self.planned: list[PlannedForge] = []

    def _hold(self, forge: ConfiguredForge) -> None:
        self._by_id[forge.forge_id] = forge
        self._by_origin[forge.fields.api_base] = forge

    def foresee(self, choice: ForgeChoice, rules: ForgeRules, *, creating: bool) -> None:
        """Note the web origin ``choice`` declares for an API origin no forge holds, before any entry is decided. An
        entry whose addresses are malformed notes nothing; :meth:`decide` refuses it."""
        if choice.forge is not UNSET or (choice.api_base is UNSET and not (creating and rules.defaults_to_github)):
            return
        try:
            origin = _api_origin(None if choice.api_base is UNSET else choice.api_base, rules)
            web = _web_origin(choice, rules)
        except ConfigFieldError:
            return
        if web is not None and origin not in self._by_origin:
            self._webs.setdefault(origin, web)

    def decide(
        self,
        choice: ForgeChoice,
        rules: ForgeRules,
        *,
        provider: str | None,
        current: ForgeId | None,
        section: str,
        index: int,
    ) -> ResolvedForge | None:
        """:func:`decide_forge` for one entry — at ``index`` of ``section`` — planning the forge it names when none
        holds its origin; ``None`` when an edit names nothing."""
        decision = decide_forge(
            choice,
            rules,
            provider=provider,
            current=None if current is None else self._by_id.get(current),
            holder_of=self._by_origin.get,
        )
        if decision is None:
            return None
        if isinstance(decision, ForgeFound):
            return ResolvedForge(decision.forge, decision.warnings())
        web = self._webs.get(decision.api_base, decision.web_base)
        if decision.web_base is not None and decision.web_base != web:
            assert web is not None
            raise _web_conflict(rules, decision.web_base, f"another entry declares for {decision.api_base}", web)
        taken = {held.slug for held in self._by_id.values()}
        forge, change = forge_for_origin(decision.api_base, web, taken, self._ctx, at=self._at, kind=decision.kind)
        self._hold(forge)
        self.planned.append(PlannedForge(section, index, forge, change))
        return ResolvedForge(forge, decision.warnings(forge))
