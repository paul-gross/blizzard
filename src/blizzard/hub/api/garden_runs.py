"""Garden routine runs are readable — the run list over a time window, and one run's own
delta. A top-level surface, distinct from `/api/routines/{routine_id}/run`, which starts
a new run rather than reading an existing one."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from blizzard.auth_core import FLEET_VIEW
from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.api.graph_names import GraphNames
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.garden.runs.history import (
    DeliveredSet,
    DeliveredSetDelta,
    RunDelta,
    RunEscalation,
    RunSummary,
)
from blizzard.hub.domain.garden.runs.window import InvalidWindowError
from blizzard.wire.garden_run import (
    AddedFindingView,
    DeliveredSetDeltaView,
    DeliveredSetView,
    GoneFindingView,
    ObservedFindingView,
    RunDeltaView,
    RunEscalationView,
    RunRowView,
)

router = APIRouter(prefix="/api", tags=["garden-runs"], dependencies=[Depends(reject_runner_principal)])


def _parse_instant(value: str, *, field: str) -> datetime:
    try:
        return as_utc(datetime.fromisoformat(value))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field} {value!r} is not a valid ISO-8601 instant",
        ) from exc


def _delivered_view(delivered: DeliveredSet) -> DeliveredSetView:
    return DeliveredSetView(
        finding_set_id=delivered.finding_set_id,
        revisions=delivered.revisions,
        measurement=delivered.measurement,
        added_count=delivered.added_count,
        observed_count=delivered.observed_count,
        gone_count=delivered.gone_count,
    )


def _escalation_view(escalation: RunEscalation | None, names: GraphNames) -> RunEscalationView | None:
    if escalation is None:
        return None
    return RunEscalationView(
        node_name=names.node_name(escalation.graph_id, escalation.node_id),
        takeover_command=escalation.takeover_command,
        wrapped_takeover_command=escalation.wrapped_takeover_command,
    )


def _run_row_view(row: RunSummary, names: GraphNames) -> RunRowView:
    return RunRowView(
        chunk_id=row.chunk_id,
        routine_name=row.routine_name,
        scope_slug=row.scope_slug,
        mode=row.mode,
        minted_at=iso_utc(row.minted_at),
        outcome=row.outcome,
        escalation=_escalation_view(row.escalation, names),
        delivered=[_delivered_view(d) for d in row.delivered],
    )


def _set_delta_view(delta: DeliveredSetDelta) -> DeliveredSetDeltaView:
    return DeliveredSetDeltaView(
        finding_set_id=delta.finding_set_id,
        revisions=delta.revisions,
        measurement=delta.measurement,
        added=[
            # `class_`'s alias is the Python keyword `class` — constructed by alias via
            # `model_validate`, the `findings.py` `finding_view` shape.
            AddedFindingView.model_validate(
                {
                    "finding_id": a.finding_id,
                    "class": a.class_,
                    "locus": a.locus,
                    "summary": a.summary,
                    "introduced": a.introduced,
                }
            )
            for a in delta.added
        ],
        observed=[
            # `class_`'s alias is the Python keyword `class`, the added group's own shape.
            ObservedFindingView.model_validate(
                {"finding_id": o.finding_id, "class": o.class_, "locus": o.locus, "summary": o.summary}
            )
            for o in delta.observed
        ],
        gone=[GoneFindingView(finding_id=g.finding_id, note=g.note) for g in delta.gone],
    )


def _run_delta_view(delta: RunDelta, names: GraphNames) -> RunDeltaView:
    return RunDeltaView(
        chunk_id=delta.chunk_id,
        routine_name=delta.routine_name,
        scope_slug=delta.scope_slug,
        mode=delta.mode,
        outcome=delta.outcome,
        escalation=_escalation_view(delta.escalation, names),
        sets=[_set_delta_view(s) for s in delta.sets],
    )


@router.get("/runs", response_model=list[RunRowView], dependencies=[Depends(require(FLEET_VIEW))])
def list_runs(
    services: Annotated[HubServices, Depends(get_services)],
    since: Annotated[str | None, Query()] = None,
    until: Annotated[str | None, Query()] = None,
) -> list[RunRowView]:
    """Every routine run minted in `[since, until)`, newest first — `since`/`until`
    default to the last 24 hours ending now. Each row carries its routine, scope, mode,
    its derived outcome, and, where it delivered, every finding-set row it published.
    422 on a malformed instant or an inverted span."""
    parsed_until = _parse_instant(until, field="until") if until is not None else None
    parsed_since = _parse_instant(since, field="since") if since is not None else None
    try:
        rows = services.garden_run.list_runs(since=parsed_since, until=parsed_until)
    except InvalidWindowError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    names = GraphNames(services.graphs)
    names.prime(row.escalation.graph_id for row in rows if row.escalation is not None)
    return [_run_row_view(row, names) for row in rows]


@router.get("/runs/{chunk_id}", response_model=RunDeltaView, dependencies=[Depends(require(FLEET_VIEW))])
def run_delta(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> RunDeltaView:
    """One run's full detail: its routine, scope, mode, derived outcome, and, per
    finding-set it delivered, the added/observed/gone entries its own artifact
    published. 404 when `chunk_id` names no routine run."""
    chunk = services.chunks.record.get(chunk_id)
    delta = services.garden_run.run_delta(chunk) if chunk is not None else None
    if delta is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown run {chunk_id}")
    names = GraphNames(services.graphs)
    if delta.escalation is not None:
        names.prime([delta.escalation.graph_id])
    return _run_delta_view(delta, names)
