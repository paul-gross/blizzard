"""The configuration surface as served — the change log (``GET /api/config/changes``) and the
declarative document (``POST /api/config/apply``, ``GET /api/config/export``)."""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from blizzard.foundation.landing_policy import LandingPolicy
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.cadence import CadenceDocument
from blizzard.wire.forge import DEPRECATED_FIELD, OldFieldWarning
from blizzard.wire.problem import CommonProblemMembers, ProblemDetails, ValidationEntry
from blizzard.wire.routine import RoutineDocument
from blizzard.wire.scope import ScopeDocument
from blizzard.wire.urgency import UrgencyIconDocument
from blizzard.wire.vocabulary import OPEN


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
    #: Every change the request's filters select, whatever ``before``.
    total: int = 0


class ProjectDocument(BaseModel):
    """A project as a document entry, named by its slug; a field it omits is left as stored, and a slug a project left
    is refused. ``sources``, ``repositories`` and ``repository_branches`` are each the project's whole set of that kind,
    keyed by record id or slug; ``landing_order`` ranks linked repositories, first to land first. Both apply after the
    links, and export writes each only when set (``docs/deployment/config-documents.md``)."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str | None = None
    description: str = ""
    color: str | None = None
    sources: list[str] = []
    repositories: list[str] = []
    landing_order: list[str] | None = Field(default=None, exclude_if=lambda value: value is None)
    repository_branches: dict[str, str] | None = Field(default=None, exclude_if=lambda value: value is None)


class ScopeEntry(ScopeDocument):
    """A scope entry of a document: the scope, in ``project`` — an id or a slug. With no ``project``, the
    tenant's only project, counted as the document leaves the tenant; with several, the entry is refused
    ``project_required``."""

    project: str | None = None


class RoutineEntry(RoutineDocument):
    """A routine entry of a document: the routine, in ``project`` — an id or a slug; named as a scope entry's is.
    Its default and linked scopes are scopes of that project."""

    project: str | None = None


class CadenceEntry(CadenceDocument):
    """A cadence entry of a document: the cadence, in ``project`` — an id or a slug; named as a scope entry's is.
    Its chores name routines and scopes of that project, by slug."""

    project: str | None = None


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
    landing_policy: LandingPolicy | None = None
    park_timeout_hours: int | None = None


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


class UrgencyEntry(BaseModel):
    """An urgency as a document entry, named by its slug: created when the tenant holds none by it — with no ``name``
    taking the slug and no ``color`` the palette color the live urgencies use least — and otherwise a sparse edit of
    the fields it states. ``null`` on ``emoji`` or ``icon`` clears the mark; an ``icon`` is its media type and base64
    bytes. An entry naming a retired urgency is refused ``urgency_retired``."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str | None = None
    color: str | None = None
    emoji: str | None = None
    icon: UrgencyIconDocument | None = None


class SettingsEntry(BaseModel):
    """The tenant's settings as a document states them: ``default_urgency``, by id or slug, sets the urgency every new
    chunk takes when nothing names another; ``null`` is refused."""

    model_config = ConfigDict(extra="forbid")

    default_urgency: str | None = None


class ConfigDocumentV2(BaseModel):
    """A declarative configuration document, version 2 — the one version a hub applies and exports. Each entry is the
    kind's own document model, so a field an entry omits is left as stored; forges reconcile before the records that
    name them, projects before scopes and routines, ``urgencies``, ``urgency_order`` and ``settings`` in that order
    after the projects and before the scopes, and scopes before routines, so an entry may name a forge, a project, an
    urgency or a scope the same document declares; ``secrets`` lists secret names that must already be active.
    ``GET /api/config/export`` writes every field of every entry, ``projects`` first, and names every project by its
    slug."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[2]
    projects: list[ProjectDocument] = []
    urgencies: list[UrgencyEntry] = []
    #: The whole live order, most urgent first, naming every urgency live once the apply has run; omitted, it stands.
    urgency_order: list[str] | None = None
    settings: SettingsEntry | None = None
    forges: list[ForgeEntry] = []
    secrets: list[str] = []
    work_sources: list[WorkSourceEntry] = []
    repositories: list[RepositoryEntry] = []
    scopes: list[ScopeEntry] = []
    routines: list[RoutineEntry] = []
    cadences: list[CadenceEntry] = []


class ConfigApplyOutcome(WireModel):
    """One outcome of an apply. ``op`` is ``create``, ``edit`` or ``enable`` for a change written and
    ``unchanged`` for a named record that needed none; ``diff`` is empty for ``unchanged``."""

    kind: str
    key: str
    op: str
    diff: list[FieldChangeView] = []


class ConfigApplyResponse(WireModel):
    """What an apply did, or — under ``dry_run`` — would do, with the identical ``outcomes``.
    ``apply_id`` groups the change rows a real apply wrote; ``None`` for a dry run."""

    dry_run: bool
    apply_id: str | None = None
    outcomes: list[ConfigApplyOutcome]
    #: Always empty.
    warnings: list[OldFieldWarning] = Field(default=[], json_schema_extra=DEPRECATED_FIELD)


class ConfigApplyConflictProblem(ProblemDetails[Literal["urn:blizzard:problem:config-apply-conflict"]]):
    """An apply's entry conflicts with a record the tenant holds; ``detail`` names the entry."""


class ConfigMediaTypeUnsupportedProblem(ProblemDetails[Literal["urn:blizzard:problem:config-media-type-unsupported"]]):
    """An apply's ``Content-Type`` names no document format the hub reads."""


class ConfigDocumentError(BaseModel):
    """One located entry of an invalid configuration document: ``loc`` is the path inside the request. A decode error
    also carries ``line`` and ``column``; a ``project_required`` entry carries ``projects`` to choose from."""

    model_config = ConfigDict(extra="allow")

    loc: list[str | int]
    msg: str
    type: str


class ConfigDocumentInvalidProblem(
    CommonProblemMembers[Literal["urn:blizzard:problem:config-document-invalid"], list[ConfigDocumentError]]
):
    """A configuration document, or the version a read names, the hub refuses; ``detail`` locates every error."""

    status: int | None = None


CONFIG_APPLY_CONFLICT: Final = "urn:blizzard:problem:config-apply-conflict"


CONFIG_MEDIA_TYPE_UNSUPPORTED: Final = "urn:blizzard:problem:config-media-type-unsupported"


CONFIG_DOCUMENT_INVALID: Final = "urn:blizzard:problem:config-document-invalid"


CONFIG_SCHEMA_NOT_FOUND: Final = "urn:blizzard:problem:config-schema-not-found"


CONFIG_FIELD_INVALID: Final = "urn:blizzard:problem:config-field-invalid"


class ConfigSchemaNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:config-schema-not-found"]]):
    """No document schema exists for the named record kind."""


class ConfigFieldInvalidProblem(
    CommonProblemMembers[Literal["urn:blizzard:problem:config-field-invalid"], list[ValidationEntry]]
):
    """A configured record's field cannot mean anything; ``detail`` locates it in the body."""

    status: int | None = None


CONFIG_PROBLEM_TYPES: frozenset[str] = frozenset(
    {
        CONFIG_APPLY_CONFLICT,
        CONFIG_MEDIA_TYPE_UNSUPPORTED,
        CONFIG_DOCUMENT_INVALID,
        CONFIG_SCHEMA_NOT_FOUND,
        CONFIG_FIELD_INVALID,
    }
)


class LandingPolicyNeedsView(WireModel):
    """What one landing policy needs of its forge: the capabilities it cannot land without (``requires``) and the ones
    it uses when the forge offers them (``uses``), each sorted."""

    policy: Annotated[LandingPolicy | Unrecognized, OPEN]
    requires: list[str]
    uses: list[str]
