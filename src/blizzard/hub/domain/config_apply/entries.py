"""What a declared document asks of an apply, before any id is minted or any stored record is read.

The edge decodes the wire document into these entries; ``ConfigApplication`` (``config_apply.service``) turns them
into the declarations the per-kind plans take."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.forge_resolution import ForgeChoice
from blizzard.hub.domain.config.repositories import RepositoryDraft, RepositoryEdit
from blizzard.hub.domain.config.work_sources import WorkSourceDraft, WorkSourceEdit
from blizzard.hub.domain.garden.routines import RoutineEdit
from blizzard.hub.domain.garden.scopes import ScopeEdit
from blizzard.hub.domain.projects.declarations import ProjectDeclaration


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
class DeclaredScope:
    """A scope the document names by slug within ``project`` — a project slug, ``None`` for the tenant's only one."""

    name: str
    description: str
    edit: ScopeEdit
    project: str | None


@domain_model
@dataclass(frozen=True)
class DeclaredRoutine:
    """A routine the document names within ``project`` — a project slug, ``None`` for the tenant's only one. A
    ``scopes`` of ``None`` leaves the stored linked scope set."""

    name: str
    graph_name: str
    default_scope_slug: str
    default_model: Sequence[str]
    default_effort: str | None
    default_harnesses: Sequence[str]
    edit: RoutineEdit
    project: str | None
    scopes: Sequence[str] | None = None


@domain_model
@dataclass(frozen=True)
class DocumentEntries:
    """Every entry of one document, by section, as its edge decoded them."""

    secrets: tuple[str, ...] = ()
    work_sources: tuple[DeclaredWorkSource, ...] = ()
    repositories: tuple[DeclaredRepository, ...] = ()
    projects: tuple[ProjectDeclaration, ...] = ()
    scopes: tuple[DeclaredScope, ...] = ()
    routines: tuple[DeclaredRoutine, ...] = ()
