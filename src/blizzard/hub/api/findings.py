"""Finding routes (blizzard#390; write verbs blizzard#394 Phase 2).

``GET /api/findings`` is the pass's own bucket read
(blizzard-product:/plans/garden/machinery.md §Managing findings and proposals): a
routine's live findings under one scope. The `POST /api/findings/{verb}` routes are the
human-driven exit verbs and `reopen`, delegating to `FindingExitService`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from blizzard.auth_core import CHUNK_CONTROL, FLEET_VIEW
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.findings import Finding, FindingNoteRequiredError
from blizzard.wire.finding import FindingExitRequest, FindingSupersedeRequest, FindingView

router = APIRouter(prefix="/api", tags=["findings"], dependencies=[Depends(reject_runner_principal)])


def _finding_view(finding: Finding) -> FindingView:
    # `class_`'s alias is the Python keyword `class` — constructed by alias via
    # `model_validate`, the `_proposal_view` shape.
    return FindingView.model_validate(
        {
            "finding_id": finding.finding_id,
            "routine_name": finding.routine_name,
            "scope_slug": finding.scope_slug,
            "class": finding.class_,
            "locus": finding.locus,
            "summary": finding.summary,
            "introduced": finding.introduced,
            "live": finding.live,
            "state": finding.state,
            "note": finding.note,
            "last_seen_at": iso_utc(finding.last_seen_at) if finding.last_seen_at is not None else None,
            "observed_count": finding.observed_count,
        }
    )


def _load_or_404(finding_ids: list[str], services: HubServices) -> list[Finding]:
    """`finding_ids`, in order — one batched read (`get_many`), not one query per id."""
    by_id = services.findings.get_many(finding_ids)
    findings = []
    for finding_id in finding_ids:
        finding = by_id.get(finding_id)
        if finding is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown finding {finding_id}")
        findings.append(finding)
    return findings


def _reread(findings: list[Finding], services: HubServices) -> list[FindingView]:
    """Every named finding, re-read post-write, so the response carries the fresh state
    and note an exit verb just recorded rather than the stale pre-call snapshot — one
    batched read (`get_many`), not one query per finding."""
    by_id = services.findings.get_many([f.finding_id for f in findings])
    views = []
    for f in findings:
        fresh = by_id.get(f.finding_id)
        assert fresh is not None  # just written a fact against it; cannot have vanished
        views.append(_finding_view(fresh))
    return views


@router.get("/findings", response_model=list[FindingView], dependencies=[Depends(require(FLEET_VIEW))])
def list_findings(
    services: Annotated[HubServices, Depends(get_services)],
    routine: Annotated[str, Query()],
    scope: Annotated[str, Query()],
    include_gone: Annotated[bool, Query()] = False,
) -> list[FindingView]:
    """A routine's findings under one scope — live only, unless `include_gone` (D3),
    which also surfaces every exited finding, not just a merely `gone` one; the read a
    running pass calls to cross-reference its own bucket."""
    return [_finding_view(f) for f in services.findings.list_for(routine, scope, include_gone=include_gone)]


@router.get("/findings/{finding_id}", response_model=FindingView, dependencies=[Depends(require(FLEET_VIEW))])
def get_finding(finding_id: str, services: Annotated[HubServices, Depends(get_services)]) -> FindingView:
    """One finding's whole record; 404 on an unknown id."""
    finding = services.findings.get(finding_id)
    if finding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown finding {finding_id}")
    return _finding_view(finding)


def _exit_verb(
    request: FindingExitRequest,
    services: HubServices,
    identity: ResolvedIdentity,
    verb: Callable[..., None],
) -> list[FindingView]:
    """The shape every single-note exit verb shares: load or 404, apply `verb`, 422 on a
    blank note, re-read. `supersede` carries extra validation of its own and stays apart."""
    findings = _load_or_404(request.finding_ids, services)
    try:
        verb(findings, note=request.note, actor=identity.user_id)
    except FindingNoteRequiredError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _reread(findings, services)


@router.post("/findings/resolve", response_model=list[FindingView])
def resolve_findings(
    request: FindingExitRequest,
    services: Annotated[HubServices, Depends(get_services)],
    identity: Annotated[ResolvedIdentity, Depends(require(CHUNK_CONTROL))],
) -> list[FindingView]:
    """Resolve every finding in `finding_ids`, recording NOTE: the work that answers a
    finding landed. 404 for an unknown id, 422 for a blank note. A hand resolution names
    no garden proposal — that attribution is Phase 3's own, delivery-triggered."""
    return _exit_verb(request, services, identity, services.finding_exit.resolve)


@router.post("/findings/confirm-gone", response_model=list[FindingView])
def confirm_gone_findings(
    request: FindingExitRequest,
    services: Annotated[HubServices, Depends(get_services)],
    identity: Annotated[ResolvedIdentity, Depends(require(CHUNK_CONTROL))],
) -> list[FindingView]:
    """Confirm by hand that every finding in `finding_ids` no longer reproduces,
    recording NOTE. 404 for an unknown id, 422 for a blank note."""
    return _exit_verb(request, services, identity, services.finding_exit.confirm_gone)


@router.post("/findings/wont-fix", response_model=list[FindingView])
def wont_fix_findings(
    request: FindingExitRequest,
    services: Annotated[HubServices, Depends(get_services)],
    identity: Annotated[ResolvedIdentity, Depends(require(CHUNK_CONTROL))],
) -> list[FindingView]:
    """Withdraw every finding in `finding_ids` as won't-fix, recording NOTE: the ground
    hasn't moved, a person has decided it doesn't merit standing regardless. 404 for an
    unknown id, 422 for a blank note."""
    return _exit_verb(request, services, identity, services.finding_exit.wont_fix)


@router.post("/findings/not-a-finding", response_model=list[FindingView])
def not_a_finding_findings(
    request: FindingExitRequest,
    services: Annotated[HubServices, Depends(get_services)],
    identity: Annotated[ResolvedIdentity, Depends(require(CHUNK_CONTROL))],
) -> list[FindingView]:
    """Withdraw every finding in `finding_ids` as not a finding, recording NOTE. 404 for
    an unknown id, 422 for a blank note."""
    return _exit_verb(request, services, identity, services.finding_exit.not_a_finding)


@router.post("/findings/supersede", response_model=list[FindingView])
def supersede_findings(
    request: FindingSupersedeRequest,
    services: Annotated[HubServices, Depends(get_services)],
    identity: Annotated[ResolvedIdentity, Depends(require(CHUNK_CONTROL))],
) -> list[FindingView]:
    """Withdraw every finding in `finding_ids` as superseded by `superseded_by`,
    recording NOTE. 404 for an unknown id in either `finding_ids` or `superseded_by`, 422
    for a blank note, a self-superseding id, or a `superseded_by` that isn't itself live."""
    if request.superseded_by in request.finding_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"finding {request.superseded_by!r} cannot supersede itself",
        )
    findings = _load_or_404(request.finding_ids, services)
    (absorber,) = _load_or_404([request.superseded_by], services)
    if not absorber.live:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"finding {request.superseded_by!r} is not live and cannot absorb another finding",
        )
    try:
        services.finding_exit.supersede(
            findings, note=request.note, actor=identity.user_id, superseded_by=request.superseded_by
        )
    except FindingNoteRequiredError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _reread(findings, services)


@router.post("/findings/reopen", response_model=list[FindingView])
def reopen_findings(
    request: FindingExitRequest,
    services: Annotated[HubServices, Depends(get_services)],
    identity: Annotated[ResolvedIdentity, Depends(require(CHUNK_CONTROL))],
) -> list[FindingView]:
    """Reopen every finding in `finding_ids`, undoing whichever exit or `gone` fact was
    newest, recording NOTE. 404 for an unknown id, 422 for a blank note."""
    return _exit_verb(request, services, identity, services.finding_exit.reopen)
