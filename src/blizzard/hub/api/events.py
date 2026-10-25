"""The hub live-event stream — ``GET /api/events/stream`` (SSE), excluded from the OpenAPI schema.

A subscriber registers with its tenant's :class:`~blizzard.hub.events.broker.EventBroker`, replays
the buffered tail newer than its ``Last-Event-ID``, then streams live until it disconnects, its
tenant's broker is dropped, or its viewer's membership in the tenant ends — ids are monotonic, so an
event caught in both is emitted once.
:class:`Cursor`/:class:`Stream` are the runner-shared machinery; only the reserved open-of-stream
comment names this daemon."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from blizzard.auth_core import Permission
from blizzard.foundation.event_log import EventLogSeverity
from blizzard.foundation.events.stream import Cursor, CursorRefused, Stream
from blizzard.foundation.roles import dto
from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.deps import Services
from blizzard.hub.api.project_params import QueryProject
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.chunk.model import ActivityEntry, ActivityFeed, EventFeed, OperationalEvent
from blizzard.hub.domain.chunk.ports.events import FeedLens
from blizzard.hub.domain.kernel.pagination import DEFAULT_LIMIT
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.events.project_lens import ProjectLens, runners_serving
from blizzard.wire.activity import ActivityResponse, ActivityView
from blizzard.wire.events import EventsResponse, EventView

router = APIRouter(prefix="/api", tags=["meta"], dependencies=[Depends(tenant_bound)])

_RESERVED_COMMENT = ": blizzard hub event stream\n\n"
# These two reads are bounded below the hub's general list maximum: 200 is a real ceiling here.
FEED_MAX_LIMIT = 200


@router.get("/events/stream", include_in_schema=False, dependencies=[Depends(reject_runner_principal)])
async def events_stream(
    request: Request,
    services: Services,
    project: QueryProject = None,
    viewer: Annotated[ResolvedIdentity | None, Depends(require(Permission.FLEET_VIEW))] = None,
) -> StreamingResponse:
    """Subscribe to the live event stream of the request's tenant, resuming from ``Last-Event-ID`` if
    present. The stream ends when the tenant's broker is dropped, or when the viewer's membership in the
    tenant ends. ``?project=`` sends only the frames :class:`ProjectLens` selects for that project, the
    replay tail's included. ``?from=latest`` skips the replay ring and sends only events published after the
    subscription registered; an unknown ``from`` value, or ``from=latest`` with ``?last_event_id=``, is a ``422``."""
    cursor = _cursor(request)
    shutdown: asyncio.Event | None = getattr(request.app.state, "shutdown", None)
    admits = None if project is None else (await _stream_lens(services, project)).admits
    owner = viewer.user_id if viewer is not None else None
    stream = Stream(services.events, request, cursor, _RESERVED_COMMENT, shutdown, admits=admits, owner=owner)
    return StreamingResponse(stream.frames(), media_type="text/event-stream")


def _cursor(request: Request) -> Cursor:
    try:
        return Cursor.of(request)
    except CursorRefused as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


async def _stream_lens(services: TenantServices, project: ProjectId) -> ProjectLens:
    """The stream's lens for ``project``, its served-runner set read off the event loop, now and on each refresh."""

    async def serving() -> frozenset[str]:
        return await run_in_threadpool(runners_serving, services.registry, project)

    return ProjectLens(project, await serving(), serving)


def _feed_lens(services: TenantServices, project: ProjectId | None) -> FeedLens | None:
    """The lens ``?project=`` puts on a feed — ``None`` when the feed reads the whole tenant."""
    return None if project is None else FeedLens(project, runners_serving(services.registry, project))


def _runner_names(services: TenantServices, runner_ids: Iterable[str | None]) -> dict[str, str]:
    """The registered names of the runners a feed's rows name — one batched registry read."""
    return services.registry.names_for(runner_id for runner_id in runner_ids if runner_id is not None)


def _event_view(row: OperationalEvent, runner_names: Mapping[str, str]) -> EventView:
    return EventView(
        id=row.id,
        recorded_at=iso_utc(row.recorded_at),
        severity=row.severity,
        kind=row.kind,
        runner_id=row.runner_id,
        runner_name=runner_names.get(row.runner_id) if row.runner_id is not None else None,
        chunk_id=row.chunk_id,
        lease_id=row.lease_id,
        node_name=row.node_name,
        message=row.message,
        detail=row.detail,
    )


@dto
@dataclass(frozen=True)
class Events:
    rows: Sequence[OperationalEvent]
    #: The rows' runners' registered names, keyed by runner id.
    runner_names: Mapping[str, str]

    def response(self) -> EventsResponse:
        return EventsResponse(events=[_event_view(row, self.runner_names) for row in self.rows])


@router.get(
    "/events",
    response_model=EventsResponse,
    dependencies=[Depends(reject_runner_principal), Depends(require(Permission.FLEET_VIEW))],
)
def list_events(
    services: Services,
    severity: Annotated[EventLogSeverity | None, Query()] = None,
    runner_id: Annotated[str | None, Query()] = None,
    chunk_id: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=FEED_MAX_LIMIT)] = DEFAULT_LIMIT,
    project: QueryProject = None,
) -> EventsResponse:
    """The ``event_log`` unified with open escalations, newest first, bounded.

    The ``severity`` / ``runner_id`` / ``chunk_id`` / ``since`` / ``project`` filters apply to both halves, the
    event log and the open escalations alike. Under ``project``, a row naming a chunk is kept when the chunk is the
    project's, a row naming only a runner when that runner serves it, and a row naming neither always. A tz-naive
    ``since`` is coerced to UTC so the escalations' aware ``recorded_at`` comparison never raises against it."""
    since_utc = as_utc(since) if since is not None else None
    events = services.chunks.events.list_events(
        severity=severity,
        runner_id=runner_id,
        chunk_id=chunk_id,
        since=since_utc,
        limit=limit,
        lens=_feed_lens(services, project),
    )
    # Escalations project as critical, runner-less events. Without a chunk filter, their
    # candidate scan is intentional: open-ness is derived by ChunkFacts and volume is low.
    escalations = (
        [
            e
            for e in services.chunks.escalations.list_open_escalations(project=project, chunk_id=chunk_id)
            if e.matches(severity=severity, runner_id=runner_id, chunk_id=chunk_id, since=since_utc)
        ]
        if (severity is None or severity is EventLogSeverity.CRITICAL) and runner_id is None
        else []
    )
    rows = EventFeed.of(events, escalations).rows[:limit]
    return Events(rows, _runner_names(services, (row.runner_id for row in rows))).response()


def _activity_view(row: ActivityEntry, runner_names: Mapping[str, str]) -> ActivityView:
    return ActivityView(
        type=row.type,
        key=row.key,
        at=iso_utc(row.at),
        chunk_id=row.chunk_id,
        status=row.status,
        prev_status=row.prev_status,
        node=row.node,
        prev_node=row.prev_node,
        runner_id=row.runner_id,
        runner_name=runner_names.get(row.runner_id) if row.runner_id is not None else None,
        cause=row.cause,
        graph_id=row.graph_id,
        severity=row.severity,
        kind=row.kind,
        by=row.by,
        reason=row.reason,
    )


@dto
@dataclass(frozen=True)
class Activity:
    rows: Sequence[ActivityEntry]
    #: The rows' runners' registered names, keyed by runner id.
    runner_names: Mapping[str, str]

    def response(self) -> ActivityResponse:
        return ActivityResponse(activity=[_activity_view(row, self.runner_names) for row in self.rows])


@router.get(
    "/activity",
    response_model=ActivityResponse,
    dependencies=[Depends(reject_runner_principal), Depends(require(Permission.FLEET_VIEW))],
)
def list_activity(
    services: Services,
    since: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=FEED_MAX_LIMIT)] = DEFAULT_LIMIT,
    project: QueryProject = None,
) -> ActivityResponse:
    """The activity backfill — the three already-bounded per-source activity reads merged,
    sorted newest-first, and capped. ``project`` narrows each source as ``GET /api/events`` does: chunk facts
    to the project's chunks, a runner's pause facts to the runners serving it.

    ``since`` defaults to 24h before the server's current time. A tz-naive ``since`` is coerced to
    UTC so it never raises against the store's aware timestamps."""
    since_utc = as_utc(since) if since is not None else services.clock.now() - timedelta(hours=24)
    lens = _feed_lens(services, project)
    chunk_changed = services.chunks.events.activity_facts_since(since_utc, limit=limit, project=project)
    events = services.chunks.events.activity_events_since(since_utc, limit=limit, lens=lens)
    runner_changed = services.registry.list_pause_facts_since(
        since_utc, limit=limit, runners=lens.runners if lens is not None else None
    )
    rows = ActivityFeed.of(chunk_changed, events, runner_changed, limit=limit).rows
    return Activity(rows, _runner_names(services, (row.runner_id for row in rows))).response()
