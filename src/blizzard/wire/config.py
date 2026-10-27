"""The configuration surface as served — the change log (``GET /api/config/changes``) and the
declarative document (``POST /api/config/apply``, ``GET /api/config/export``)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from blizzard.wire.base import WireModel
from blizzard.wire.forge import OldFieldWarning
from blizzard.wire.repository import RepositoryDocument
from blizzard.wire.routine import RoutineDocument
from blizzard.wire.scope import ScopeDocument
from blizzard.wire.work_source import WorkSourceDocument


class FieldChangeView(WireModel):
    """One changed field. A secret's value never appears in a diff."""

    field: str
    old: object = None
    new: object = None
    old_label: str | None = None
    new_label: str | None = None


class ConfigChangeView(WireModel):
    """One row of the change log."""

    id: int
    recorded_at: str
    actor: str
    #: The actor's display name, falling back to their username; ``None`` when the actor is not a user.
    actor_name: str | None = None
    door: str
    record_kind: str
    record_key: str
    #: The record's current slug for a slugged kind, whose key is its id; ``None`` for a kind keyed by its name.
    record_label: str | None = None
    revision: int
    op: str
    diff: list[FieldChangeView]
    apply_id: str | None = None
    #: The project the change concerns — a project's own, its links', a scope's or a routine's; ``None`` for a
    #: tenant record.
    project_id: str | None = None


class ConfigChangesPage(WireModel):
    """A newest-first page; ``next_before`` is the ``before`` that fetches the next, ``None`` at the end."""

    changes: list[ConfigChangeView]
    next_before: int | None = None
    #: Every change the request's filters select, whatever ``before`` — the figure a count badge reads.
    total: int = 0


class ProjectDocument(BaseModel):
    """A project as a document entry, named by its slug. A ``name``, ``description`` or ``color`` the entry omits is
    left as stored; a project the document creates needs a ``name`` and, with no ``color``, takes the tenant's
    least-used palette color; a ``color`` is a palette name or a ``#rrggbb``. A ``sources`` or ``repositories`` list
    the entry states is the project's whole set of links of that kind — a record it leaves out is unlinked; a list it
    omits is left as stored. A source or repository is named by its id or its slug, a slug it left included. A slug a
    project left is refused: name that project by its current slug."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str | None = None
    description: str = ""
    color: str | None = None
    sources: list[str] = []
    repositories: list[str] = []


class ScopeEntry(ScopeDocument):
    """A scope entry of a document: the scope, in ``project`` — an id or a slug. With no ``project``, the
    tenant's only project, counted as the document leaves the tenant; with several, the entry is refused
    ``project_required``."""

    project: str | None = None


class RoutineEntry(RoutineDocument):
    """A routine entry of a document: the routine, in ``project`` — an id or a slug; named as a scope entry's is.
    Its default and linked scopes are scopes of that project."""

    project: str | None = None


class ConfigDocument(BaseModel):
    """A declarative configuration document. Each entry is the kind's own document model, so a field an
    entry omits is left as stored; projects reconcile before scopes and routines, and scopes before
    routines, so an entry may name a project or a scope the same document declares; ``secrets`` lists
    secret names that must already be active. ``GET /api/config/export`` writes every field of every
    entry, ``projects`` first, and names every project by its slug."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    projects: list[ProjectDocument] = []
    secrets: list[str] = []
    work_sources: list[WorkSourceDocument] = []
    repositories: list[RepositoryDocument] = []
    scopes: list[ScopeEntry] = []
    routines: list[RoutineEntry] = []


class ForgeEntry(BaseModel):
    """A forge declared by slug; an omitted mutable field stays as stored."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str | None = None
    kind: str
    api_base: str
    web_base: str | None = None


class RepositoryEntry(BaseModel):
    """A version-2 repository entry, naming its forge instead of an address."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str | None = None
    forge: str
    owner: str
    repo: str
    base_branch: str
    secret_name: str


class WorkSourceEntry(BaseModel):
    """A version-2 work source entry, naming its forge instead of addresses."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str | None = None
    provider: str
    locator: str
    forge: str
    annotate: bool = False
    secret: str | None = None


class ConfigDocumentV2(BaseModel):
    """A declarative document whose forge records and references are explicit."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[2]
    projects: list[ProjectDocument] = []
    forges: list[ForgeEntry] = []
    secrets: list[str] = []
    work_sources: list[WorkSourceEntry] = []
    repositories: list[RepositoryEntry] = []
    scopes: list[ScopeEntry] = []
    routines: list[RoutineEntry] = []


class ConfigApplyOutcome(WireModel):
    """One outcome of an apply. ``op`` is ``create``, ``edit`` or ``enable`` for a change written and
    ``unchanged`` for a named record that needed none; ``diff`` is empty for ``unchanged``."""

    kind: str
    key: str
    op: str
    diff: list[FieldChangeView] = []


class ConfigApplyResponse(WireModel):
    """What an apply did, or — under ``dry_run`` — would do, with the identical ``outcomes``.
    ``apply_id`` groups the change rows a real apply wrote; ``None`` for a dry run. ``warnings`` names each old address
    field an entry used and the forge it resolved to."""

    dry_run: bool
    apply_id: str | None = None
    outcomes: list[ConfigApplyOutcome]
    #: One entry per old address field an entry used, naming the forge it resolved to.
    warnings: list[OldFieldWarning] = []
