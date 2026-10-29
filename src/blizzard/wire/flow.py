"""Flow wire bodies — each branch's delivery line, the places its chunks hold in it, and its record."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.vocabulary import OPEN


class FlowPlaceState(StrEnum):
    """Where an open submission stands on a flow it holds."""

    LANDING = "landing"
    NEXT = "next"
    WAITING = "waiting"
    POLLING = "polling"
    PASSED_OVER = "passed-over"


class FlowFactKind(StrEnum):
    """What one entry of a flow's record says happened."""

    SUBMITTED = "submitted"
    TURN_STARTED = "turn-started"
    TURN_ENDED = "turn-ended"
    LANDED = "landed"
    WITHDRAWN = "withdrawn"


class FlowFactView(WireModel):
    """One entry of a flow's record."""

    #: The entry's place in the record, newest highest — the ``before`` a record page is read below.
    id: int
    flow_id: str
    submission_id: str
    chunk_id: str
    kind: Annotated[FlowFactKind | Unrecognized, OPEN]
    at: str
    #: A ``turn-ended`` entry's outcome, or why a ``withdrawn`` one left; ``None`` for every other kind.
    outcome: str | None = None
    #: The revision a ``landed`` entry left its branch at; ``None`` for every other kind.
    revision: str | None = None


class FlowTurnView(WireModel):
    """The turn a flow has open now."""

    chunk_id: str
    started_at: str


class FlowPlaceView(WireModel):
    """One open submission's place in a flow's line."""

    chunk_id: str
    submission_id: str
    arrival: int
    state: Annotated[FlowPlaceState | Unrecognized, OPEN]
    #: One for the submission landing or next, else one plus the eligible submissions ahead of it on the flow.
    position: int
    #: When its next poll is due while it is ``polling``; ``None`` otherwise.
    next_poll_at: str | None = None


class ChunkFlowPlaceView(WireModel):
    """A chunk's place on one flow its open submission holds."""

    flow_id: str
    resource: str
    #: The flow's repository's current slug and name; ``None`` when the hub holds no record of it.
    repository_slug: str | None = None
    repository_name: str | None = None
    branch: str
    state: Annotated[FlowPlaceState | Unrecognized, OPEN]
    #: One for the chunk landing or next, else one plus the eligible submissions ahead of it on the flow.
    position: int
    #: When its next poll is due while it is ``polling``; ``None`` otherwise.
    next_poll_at: str | None = None


class FlowView(WireModel):
    """One flow: the branch it lands on, the turn open on it, its line in arrival order, and the newest of its
    record."""

    flow_id: str
    resource: str
    repository_id: str
    #: The repository's current slug and name; ``None`` when the hub holds no record of it.
    repository_slug: str | None = None
    repository_name: str | None = None
    branch: str
    #: The turn open on the flow now; ``None`` while none is.
    turn: FlowTurnView | None = None
    line: list[FlowPlaceView] = []
    #: The newest turn whose outcome closed its submission; ``None`` before the first.
    last_verdict: FlowFactView | None = None
    #: The newest ``landed`` entry; ``None`` before the first.
    last_landing: FlowFactView | None = None


class FlowListView(WireModel):
    """The tenant's flows, in resource order."""

    flows: list[FlowView] = []


class FlowRecordPage(WireModel):
    """A newest-first page of a flow's record; ``next_before`` is the ``before`` that fetches the next, ``None`` at
    the end."""

    facts: list[FlowFactView] = []
    next_before: int | None = None
