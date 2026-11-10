"""What a declared document asks of an apply, before any id is minted or any stored record is read."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.cadence.model import CadenceEdit, CadenceShape
from blizzard.hub.domain.config.forge_resolution import ForgeChoice
from blizzard.hub.domain.config.forges import ForgeEdit, ForgeFields
from blizzard.hub.domain.config.repositories import RepositoryDraft, RepositoryEdit
from blizzard.hub.domain.config.work_sources import WorkSourceDraft, WorkSourceEdit
from blizzard.hub.domain.garden.routines import RoutineEdit
from blizzard.hub.domain.garden.scopes import ScopeEdit
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType
from blizzard.hub.domain.projects.declarations import ProjectDeclaration
from blizzard.hub.domain.urgency.declarations import UrgencySections


@domain_model
@dataclass(frozen=True)
class DeclaredWorkSource:
    """A work source the document names by slug: the display name it states, its create fields but the forge, the
    sparse edit of what it states but the forge, and what it says of its forge."""

    slug: str
    name: str | None
    draft: WorkSourceDraft
    edit: WorkSourceEdit
    forge: ForgeChoice


@domain_model
@dataclass(frozen=True)
class DeclaredRepository:
    """A repository the document names by slug: its create fields but the forge, the sparse edit of what it states but
    the forge, and what it says of its forge."""

    slug: str
    name: str | None
    draft: RepositoryDraft
    edit: RepositoryEdit
    forge: ForgeChoice


@domain_model
@dataclass(frozen=True)
class DeclaredForge:
    slug: str
    name: str | None
    kind: str
    fields: ForgeFields
    edit: ForgeEdit


@domain_model
@dataclass(frozen=True)
class DeclaredScope:
    """A scope the document names by slug within ``project`` — a project slug, ``None`` for the tenant's only one."""

    name: str
    description: str
    edit: ScopeEdit
    project: str | None


@domain_model
@dataclass(frozen=True)
class DeclaredRoutine:
    """A routine the document names within ``project`` — a project slug, ``None`` for the tenant's only one — by
    ``slug`` or, in the older form, ``name`` alone; at least one is stated. A ``scopes`` of ``None`` leaves the
    stored linked scope set. ``default_urgency`` is the slug of the urgency it names — ``None`` for none,
    :data:`UNSET` when the entry leaves it — resolved by the apply, as ``edit`` leaves it."""

    slug: str | None
    name: str | None
    graph_name: str
    default_scope_slug: str
    default_model: Sequence[str]
    default_effort: str | None
    default_harnesses: Sequence[str]
    edit: RoutineEdit
    project: str | None
    scopes: Sequence[str] | None = None
    default_urgency: str | None | UnsetType = UNSET


@domain_model
@dataclass(frozen=True)
class DeclaredCadence:
    """A cadence the document names by slug within ``project`` — a project slug, ``None`` for the tenant's only one:
    its create shape and the sparse edit of what it states, each chore naming no urgency, and the slug of the urgency
    each chore of the entry names, in order, which the apply resolves into both."""

    shape: CadenceShape
    edit: CadenceEdit
    project: str | None
    chore_urgencies: tuple[str | None, ...] = ()


@domain_model
@dataclass(frozen=True)
class DocumentEntries:
    """Every entry of one document, by section, as its edge decoded them."""

    secrets: tuple[str, ...] = ()
    forges: tuple[DeclaredForge, ...] = ()
    work_sources: tuple[DeclaredWorkSource, ...] = ()
    repositories: tuple[DeclaredRepository, ...] = ()
    projects: tuple[ProjectDeclaration, ...] = ()
    urgencies: UrgencySections = field(default_factory=UrgencySections)
    scopes: tuple[DeclaredScope, ...] = ()
    routines: tuple[DeclaredRoutine, ...] = ()
    cadences: tuple[DeclaredCadence, ...] = ()
