"""``GET /api/env-timeline`` — every node step the routed chunks spent in a runner environment over a window —,
``GET /api/env-timeline/step``, one such step in depth, and ``GET /api/env-timeline/step/transcript``, the
transcript of the session that ran one."""

from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.foundation.usage_kind import UsageKind
from blizzard.hub.api import problems, problems_runners
from blizzard.hub.api import transcripts as transcripts_api
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.chunk_views import usage_total_view
from blizzard.hub.api.deps import Services
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.api.project_params import QueryProject
from blizzard.hub.auth.permissions import Permission
from blizzard.hub.domain.observability.env_timeline import (
    EnvSegment,
    EnvTimeline,
    PlacedSegment,
    StepDetail,
    StepNotFound,
)
from blizzard.wire.env_timeline import (
    EnvTimelineBlockView,
    EnvTimelineChunkView,
    EnvTimelineEnvironmentView,
    EnvTimelineHostView,
    EnvTimelineInvocationView,
    EnvTimelineNodeView,
    EnvTimelinePlacedStepView,
    EnvTimelineSegmentView,
    EnvTimelineStepEscalationView,
    EnvTimelineStepView,
    EnvTimelineView,
    EnvTimelineWorkspaceView,
)
from blizzard.wire.transcript_segment import LeaseTranscriptView
from blizzard.wire.vocabulary import open_value

_log = get_logger(__name__)

router = APIRouter(
    prefix="/api", tags=["env-timeline"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)]
)


def _segment(seg: EnvSegment) -> EnvTimelineSegmentView:
    return EnvTimelineSegmentView(
        chunk_id=seg.chunk_id,
        title=seg.title,
        project_id=seg.project_id,
        runner_id=seg.runner_id,
        runner_name=seg.runner_name,
        graph_id=seg.graph_id,
        graph_name=seg.graph_name,
        node_id=seg.node_id,
        node_name=seg.node_name,
        started_at=iso_utc(seg.started_at),
        ended_at=iso_utc(seg.ended_at) if seg.ended_at is not None else None,
        exit_choice=seg.exit_choice,
        failed=seg.failed,
        escalation=seg.escalation,
        escalation_open=seg.escalation_open,
        cost_usd=seg.cost_usd,
        cost_estimated=seg.cost_estimated,
        executor=seg.executor,
        epoch=seg.epoch,
        lease_id=seg.lease_id,
    )


def _block(seg: EnvSegment) -> EnvTimelineBlockView:
    return EnvTimelineBlockView(
        chunk_id=seg.chunk_id,
        runner_id=seg.runner_id,
        node_id=seg.node_id,
        started_at=iso_utc(seg.started_at),
        ended_at=iso_utc(seg.ended_at) if seg.ended_at is not None else None,
        exit_choice=seg.exit_choice,
        failed=seg.failed,
        escalation=seg.escalation,
        escalation_open=seg.escalation_open,
        cost_usd=seg.cost_usd,
        cost_estimated=seg.cost_estimated,
        executor=seg.executor,
        epoch=seg.epoch,
        lease_id=seg.lease_id,
    )


def env_timeline_view(timeline: EnvTimeline) -> EnvTimelineView:
    chunks: dict[str, EnvTimelineChunkView] = {}
    runners: dict[str, str] = {}
    nodes: dict[str, EnvTimelineNodeView] = {}
    hosts: list[EnvTimelineHostView] = []
    for host in timeline.hosts:
        workspaces: list[EnvTimelineWorkspaceView] = []
        for ws in host.workspaces:
            environments: list[EnvTimelineEnvironmentView] = []
            for env in ws.environments:
                for seg in env.segments:
                    chunks.setdefault(seg.chunk_id, EnvTimelineChunkView(title=seg.title, project_id=seg.project_id))
                    runners.setdefault(seg.runner_id, seg.runner_name)
                    nodes.setdefault(
                        seg.node_id, EnvTimelineNodeView(graph_name=seg.graph_name, node_name=seg.node_name)
                    )
                environments.append(
                    EnvTimelineEnvironmentView(
                        environment_id=env.environment_id, blocks=[_block(s) for s in env.segments]
                    )
                )
            workspaces.append(EnvTimelineWorkspaceView(workspace_id=ws.workspace_id, environments=environments))
        hosts.append(
            EnvTimelineHostView(
                host_id=host.host_id,
                host_name=host.host_name,
                runner_names=list(host.runner_names),
                workspaces=workspaces,
            )
        )
    return EnvTimelineView(
        since=iso_utc(timeline.since),
        until=iso_utc(timeline.until),
        generated_at=iso_utc(timeline.generated_at),
        hosts=hosts,
        chunks=chunks,
        runners=runners,
        nodes=nodes,
    )


@router.get("/env-timeline", response_model=EnvTimelineView, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def env_timeline(
    since: Annotated[datetime, Query()],
    services: Services,
    project: QueryProject,
    until: Annotated[datetime | None, Query()] = None,
    latest: bool = False,
) -> EnvTimelineView:
    """Every node step a routed chunk spent in a runner environment within ``[since, until)`` — ``until`` defaults
    to now, and a window wider than fourteen days is narrowed to the fourteen before ``until``. A step is sent whole
    whenever it touches the window, so a read from a client's last answer forward catches every step that has moved
    since.

    ``latest`` answers what each environment is doing now instead: only each environment's newest step since
    ``since`` (at most fourteen days back) — its current occupant, or the step it last went idle after; ``until`` is
    ignored. Under ``?project=`` only that project's chunks are laid on the environments."""
    start = as_utc(since)
    if latest:
        return env_timeline_view(services.env_timeline.read_latest(since=start, project=project))
    end = as_utc(until) if until is not None else None
    return env_timeline_view(services.env_timeline.read(since=start, until=end, project=project))


def _placed(p: PlacedSegment) -> EnvTimelinePlacedStepView:
    return EnvTimelinePlacedStepView(
        workspace_id=p.workspace_id,
        environment_id=p.environment_id,
        host_id=p.host_id,
        host_name=p.host_name,
        segment=_segment(p.segment),
    )


def step_view(detail: StepDetail) -> EnvTimelineStepView:
    return EnvTimelineStepView(
        step=_placed(detail.step),
        invocations=[
            EnvTimelineInvocationView(
                recorded_at=iso_utc(u.recorded_at),
                kind=open_value(UsageKind, u.kind),
                model=u.model,
                harness_id=u.harness_id,
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                cache_read_tokens=u.cache_read_tokens,
                cache_create_tokens=u.cache_create_tokens,
                cost_usd=u.cost_usd,
                estimated_cost_usd=u.estimated_cost_usd,
            )
            for u in detail.invocations
        ],
        escalations=[
            EnvTimelineStepEscalationView(recorded_at=iso_utc(e.recorded_at), cause=e.cause, detail=e.detail)
            for e in detail.escalations
        ],
        journey=[_placed(p) for p in detail.journey],
        generated_at=iso_utc(detail.generated_at),
        spend=usage_total_view(detail.spend) if detail.spend is not None else None,
        artifact_keys=list(detail.artifact_keys),
    )


@router.get(
    "/env-timeline/step", response_model=EnvTimelineStepView, dependencies=[Depends(require(Permission.FLEET_VIEW))]
)
def env_timeline_step(
    chunk_id: str,
    services: Services,
    lease_id: str | None = None,
    started_at: Annotated[datetime | None, Query()] = None,
) -> EnvTimelineStepView:
    """One node step of ``chunk_id`` in depth — the one the session ``lease_id`` ran, else the one starting at
    ``started_at`` (a hub step, or one whose session the hub never heard of), else the chunk's newest: where it ran,
    every harness invocation and escalation inside it, their spend, the artifacts its node recorded, and the chunk's
    whole path across the environments around it. ``404`` when the chunk is unknown, or has no such step."""
    if services.chunks.record.get(chunk_id) is None:
        raise ProblemHTTPException(partial(problems.chunk_not_found_problem, chunk_id))
    try:
        detail = services.env_timeline.step(
            chunk_id=chunk_id,
            lease_id=lease_id,
            started_at=as_utc(started_at) if started_at is not None else None,
        )
    except StepNotFound as exc:
        raise ProblemHTTPException(partial(problems_runners.node_step_not_found_problem, chunk_id)) from exc
    return step_view(detail)


@router.get(
    "/env-timeline/step/transcript",
    response_model=LeaseTranscriptView,
    dependencies=[Depends(require(Permission.TRANSCRIPT_READ))],
)
def env_timeline_step_transcript(chunk_id: str, node_id: str, epoch: int, services: Services) -> LeaseTranscriptView:
    """The transcript of the session that ran a node step of ``chunk_id`` — named by the step's ``node_id`` and
    ``epoch`` as the step read answers them: every turn the session's runner shipped, across each time the session
    was picked back up, with any part a later shipment replaced left out. ``truncated`` holds when some of it was
    lost. A session that has shipped nothing yet answers no turns; ``404`` when the chunk is unknown."""
    if services.chunks.record.get(chunk_id) is None:
        raise ProblemHTTPException(partial(problems.chunk_not_found_problem, chunk_id))
    try:
        runner_id = services.transcripts.runner_id_for_lease(chunk_id, node_id, epoch)
    except RuntimeError as exc:
        # Two runners' segments under one fence: an integrity fault elsewhere, never the caller's.
        _log.error(
            "step transcript fencing invariant violated",
            chunk_id=chunk_id,
            node_id=node_id,
            epoch=epoch,
            error=str(exc),
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "the session's transcript is in an inconsistent state"
        ) from exc
    records = services.transcripts.records_for_lease(chunk_id, node_id, epoch, runner_id) if runner_id else []
    return transcripts_api.lease_content_view(chunk_id, node_id, epoch, records)
