"""Cadence wire shapes: the cadence document and its chore, the create, edit, and lifecycle requests, and the read
view with its attention entries."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict

from blizzard.foundation.cadence_anchor import CadenceAnchor
from blizzard.foundation.cadence_attention import AttentionReason, ErrandState
from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.run_mode import RunMode
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.problem import ProblemDetails
from blizzard.wire.vocabulary import OPEN


class CadenceScheduleDocument(BaseModel):
    """The moments a cadence may fire: a five-field cron expression read as wall-clock time in an IANA ``zone``.
    Both are required; a table missing one is refused naming it."""

    model_config = ConfigDict(extra="forbid")

    cron: str | None = None
    zone: str | None = None


class ChoreDocument(BaseModel):
    """One chore of a cadence, keyed within it by ``key``. ``scopes`` is ``"all"``, a list of scope slugs, or
    absent for one run over the routine's default scope; ``mode`` is ``full`` when absent and ``promote`` true."""

    model_config = ConfigDict(extra="forbid")

    key: str
    routine: str
    scopes: Literal["all"] | list[str] | None = None
    mode: RunMode | None = None
    note: str | None = None
    promote: bool | None = None


class CadenceDocument(BaseModel):
    """A cadence as a document entry, keyed by its immutable ``slug`` — the model whose JSON Schema
    ``GET /api/config/schema/cadences`` serves. It states a ``schedule``, a ``spacing`` (a whole number and ``m``,
    ``h``, or ``d``), or both; ``anchor`` is ``scheduled``, ``started``, or ``finished``, read as ``started`` when
    absent, and only with a spacing. ``name`` is the slug when absent. A field an entry omits is left as stored, and
    ``chores``, when stated, is the whole list, matched to the stored chores by key. A chore's routine and scopes are
    named by slug and need not resolve: what does not is shown as the cadence's attention."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str | None = None
    schedule: CadenceScheduleDocument | None = None
    spacing: str | None = None
    anchor: str | None = None
    chores: list[ChoreDocument] = []


class CadenceCreateRequest(CadenceDocument):
    """Create a cadence in the path's project; its slug is free there, retired cadences included."""


class CadenceEditRequest(BaseModel):
    """A sparse edit: an absent field is unchanged, a present one is set. An explicit ``null`` clears ``schedule``,
    ``spacing``, or ``anchor`` and is refused on ``name`` and ``chores``. ``chores`` is the whole list; a chore
    whose key stays keeps its id. The slug is immutable, so a body carrying it is refused."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    schedule: CadenceScheduleDocument | None = None
    spacing: str | None = None
    anchor: str | None = None
    chores: list[ChoreDocument] | None = None


class CadenceLifecycleRequest(BaseModel):
    """Retire or re-enable a cadence; ``by`` is recorded on the lifecycle fact."""

    by: str = "operator"


class CadenceScheduleView(WireModel):
    """A cadence's schedule as stored."""

    cron: str
    zone: str


class ChoreView(WireModel):
    """One chore of a cadence, in the order written."""

    #: The ``chr_`` id minted when the key first appeared in the cadence; kept while the key stays.
    chore_id: str
    key: str
    #: The routine the chore runs, by slug.
    routine: str
    #: ``"all"``, the listed scope slugs, or ``None`` for the routine's default scope.
    scopes: Literal["all"] | list[str] | None = None
    mode: Annotated[RunMode | Unrecognized, OPEN]
    note: str | None = None
    promote: bool
    #: When the chore last changed which scopes it names.
    scopes_set_at: str


class CadenceAttentionView(WireModel):
    """One thing that stops a cadence's errands: the chore by key (``None`` for the whole cadence), the scope when
    it stops one errand, and why."""

    reason: Annotated[AttentionReason | Unrecognized, OPEN]
    chore: str | None = None
    scope: str | None = None


class ErrandCountsView(WireModel):
    """A cadence's errands counted by state. A later state is a later field."""

    due: int = 0
    running: int = 0
    waiting: int = 0
    stopped: int = 0


class CadenceView(WireModel):
    """A cadence as served by the create, list, read, edit, and lifecycle routes, with its attention as it stands at
    the read — empty when nothing stops its errands, or when it is retired."""

    cadence_id: str
    #: The cadence's handle within its project; immutable.
    slug: str
    name: str
    schedule: CadenceScheduleView | None = None
    #: The least time between an errand's runs, in minutes; ``None`` with no spacing.
    spacing_minutes: int | None = None
    #: The anchor the spacing counts from — ``started`` when unstated — or ``None`` with no spacing.
    anchor: Annotated[CadenceAnchor | Unrecognized, OPEN] | None = None
    chores: list[ChoreView] = []
    attention: list[CadenceAttentionView] = []
    #: How many of the cadence's errands stand in each state at the read.
    errand_counts: ErrandCountsView = ErrandCountsView()
    #: The schedule's next five moments after the read, earliest first; empty with no schedule or once retired.
    next_moments: list[str] = []
    created_at: str
    retired: bool = False
    revision: int
    #: The id of the project this belongs to; ``None`` where the hub reports none.
    project_id: str | None = None
    #: The project's current slug; ``None`` where the hub reports none.
    project_slug: str | None = None
    #: The project's current display name; ``None`` where the hub reports none.
    project_name: str | None = None


class ErrandRunView(WireModel):
    """An errand's last run: the chunk holding its work item (``None`` when none could be resolved), the chunk's
    status, and when the run was scheduled, started, and finished."""

    chunk_id: str | None = None
    status: Annotated[ChunkStatus | Unrecognized, OPEN] | None = None
    #: When the run's work item was minted.
    scheduled_at: str
    #: When the run's first lease was minted; ``None`` before a runner took it up.
    started_at: str | None = None
    #: When the run became terminal; ``None`` while it is live.
    finished_at: str | None = None


class CadenceErrandView(WireModel):
    """One errand of a cadence — one chore over one scope — at the read."""

    chore: str
    scope: str
    state: Annotated[ErrandState | Unrecognized, OPEN]
    #: The attention entries that stop this errand; empty when none does.
    attention: list[CadenceAttentionView] = []
    last_run: ErrandRunView | None = None
    #: When the errand is or was next due — in the past for a ``due`` errand, whose missed moment is still owed;
    #: ``None`` while it is running or stopped.
    due_at: str | None = None


class RevisionConflictProblem(ProblemDetails[Literal["urn:blizzard:problem:revision-conflict"]]):
    """A write's ``If-Match`` named a revision the record has since moved past."""
