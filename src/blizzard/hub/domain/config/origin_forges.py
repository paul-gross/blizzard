"""Which forge an address names — the legacy import's rule, the one place a record is still named by an origin.

A legacy key states addresses, never a forge: ``BZ_FORGE_URL`` for each repository, a ``[[work_source]]`` block's
``api_base`` and ``web_base`` for its source. :class:`OriginPlanner` resolves each to the tenant's forge holding its
canonical API origin, or plans one forge per origin none holds, however many keys name it and however each spells it.
A work source that states no API origin is on GitHub."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.changes import ChangeContext, ConfigChange
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forges import (
    GITHUB_API_BASE,
    ConfiguredForge,
    ForgeKindMismatch,
    ForgeUnavailable,
    canonical_field,
    forge_for_origin,
)


@domain_model
@dataclass(frozen=True)
class PlannedForge:
    """A forge the import creates for an origin no forge holds, attributed to the first key — ``section`` and ``index``
    — that names it."""

    section: str
    index: int
    record: ConfiguredForge
    change: ConfigChange


class OriginConflict(ConfigFieldError):
    """A stated web origin disagrees with the one its API origin's forge holds or another key declares."""

    code = "forge_conflict"


@domain_model
@dataclass(frozen=True)
class OriginRules:
    """How one record kind's addresses read: the name its API address goes by, the name of its web address if it
    carries one, and whether a missing API address means GitHub's."""

    api_field: str
    web_field: str | None
    defaults_to_github: bool


#: A repository's address, ``BZ_FORGE_URL``, is always stated.
REPOSITORY_ORIGIN_RULES = OriginRules(api_field="forge_api_url", web_field=None, defaults_to_github=False)
#: A work source's addresses; a source stating no API origin is on GitHub.
WORK_SOURCE_ORIGIN_RULES = OriginRules(api_field="api_base", web_field="web_base", defaults_to_github=True)


@domain_model
@dataclass(frozen=True)
class OriginChoice:
    """The addresses one key states — ``None`` where it states none."""

    api_base: str | None = None
    web_base: str | None = None


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _api_origin(value: str | None, rules: OriginRules) -> str:
    """The canonical API origin an address names; a blank one is GitHub's where the kind defaults to it."""
    if _blank(value):
        if not rules.defaults_to_github:
            raise ConfigFieldError(rules.api_field, "must not be blank")
        return GITHUB_API_BASE
    assert value is not None
    return canonical_field(rules.api_field, value)


def _web_origin(choice: OriginChoice, rules: OriginRules) -> str | None:
    """The canonical web origin ``choice`` states — ``None`` when it states none, or a blank one."""
    if rules.web_field is None or _blank(choice.web_base):
        return None
    assert choice.web_base is not None
    return canonical_field(rules.web_field, choice.web_base)


def _web_conflict(rules: OriginRules, web: str, whose: str, held: str) -> OriginConflict:
    assert rules.web_field is not None
    return OriginConflict(
        rules.web_field,
        f"{web} is not the web origin {whose}, {held}; set the forge's web_base, or leave this field out",
    )


class OriginPlanner:
    """Resolves keys' addresses against the tenant's forges — ``forges`` are every one of them, retired included —
    planning one forge per API origin none holds.

    The web origin a planned forge takes is the first explicit one a key declares for its API origin, whatever key
    first names that origin, so a key naming the origin without a web origin never refuses one declaring it later
    (:meth:`foresee`)."""

    def __init__(self, forges: Iterable[ConfiguredForge], ctx: ChangeContext, *, at: datetime) -> None:
        self._by_origin: dict[str, ConfiguredForge] = {}
        self._slugs: set[str] = set()
        for forge in forges:
            self._hold(forge)
        self._ctx = ctx
        self._at = at
        self._webs: dict[str, str] = {}
        #: Each forge planned, attributed to the key that first named it.
        self.planned: list[PlannedForge] = []

    def _hold(self, forge: ConfiguredForge) -> None:
        self._by_origin[forge.fields.api_base] = forge
        self._slugs.add(forge.slug)

    def foresee(self, choice: OriginChoice, rules: OriginRules) -> None:
        """Note the web origin ``choice`` declares for an API origin no forge holds, before any key is decided. A key
        whose addresses are malformed notes nothing; :meth:`decide` refuses it."""
        try:
            origin = _api_origin(choice.api_base, rules)
            web = _web_origin(choice, rules)
        except ConfigFieldError:
            return
        if web is not None and origin not in self._by_origin:
            self._webs.setdefault(origin, web)

    def decide(
        self, choice: OriginChoice, rules: OriginRules, *, provider: str | None, section: str, index: int
    ) -> ConfiguredForge:
        """The forge ``choice`` names for a record of ``provider`` — at ``index`` of ``section`` — planned when none
        holds its origin. :class:`ForgeUnavailable` for an origin a retired forge holds, :class:`OriginConflict` for a
        web origin other than its forge's, :class:`ForgeKindMismatch` for a forge of another kind than ``provider``,
        and :class:`ConfigFieldError` on the field whose address is not an origin."""
        kind = provider or "github"
        origin = _api_origin(choice.api_base, rules)
        web = _web_origin(choice, rules)
        forge = self._by_origin.get(origin)
        if forge is not None:
            if forge.retired:
                raise ForgeUnavailable(
                    rules.api_field, f"{origin} is held by retired forge {forge.slug}; enable that forge instead"
                )
            if web is not None and web != forge.web_origin:
                raise _web_conflict(rules, web, f"of forge {forge.slug}", forge.web_origin)
            if forge.kind != kind:
                raise ForgeKindMismatch("forge", f"forge {forge.slug} is a {forge.kind} forge, not {kind}")
            return forge
        planned_web = self._webs.get(origin, web)
        if web is not None and web != planned_web:
            assert planned_web is not None
            raise _web_conflict(rules, web, f"another entry declares for {origin}", planned_web)
        made, change = forge_for_origin(origin, planned_web, set(self._slugs), self._ctx, at=self._at, kind=kind)
        self._hold(made)
        self.planned.append(PlannedForge(section, index, made, change))
        return made
