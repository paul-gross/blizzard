"""The host's runners — ``GET /api/host/runners``, ``POST /api/host/runners`` and
``POST /api/host/runners/{runner_id}/retire``.

The list composes each served runner's own bound seams, stamping its id; none reads across runners
(``bzh:runner-scoped-store``). An add or a retire asks the hub under the host's own token, then writes the store; the
host tick starts the added runner, or drains the retired one."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from blizzard.runner.api.control import throttle_view
from blizzard.runner.api.federation import require_superuser
from blizzard.runner.api.problems import problem, reject_worker_credential
from blizzard.runner.api.settings import settings_problem
from blizzard.runner.api.wiring import RunnerWiring, ServedRunner
from blizzard.runner.auth.session import RunnerSession
from blizzard.runner.hosting.model import API
from blizzard.runner.hosting.service import (
    HostRunnerHubRefused,
    NewRunner,
    RunnerSettingsInvalid,
)
from blizzard.runner.hub.client import HostRunnerRetireRefused, HubClientError, RunnerAddRefused
from blizzard.runner.hub.identity import served_runner_state
from blizzard.wire.runner_host import (
    HostRunnerAddRequest,
    HostRunnerBindingView,
    HostRunnerListResponse,
    HostRunnerRetiredView,
    HostRunnerRetireRequest,
    HostRunnerStartFailureView,
    HostRunnerView,
)
from blizzard.wire.runner_status import PauseStateView

router = APIRouter(prefix="/api/host", tags=["host"])
writes = APIRouter(prefix="/api/host", tags=["host"], dependencies=[Depends(reject_worker_credential)])

_PROBLEM = {"application/problem+json": {"schema": {"$ref": "#/components/schemas/RunnerProblem"}}}
_SETTINGS_PROBLEM = {"application/problem+json": {"schema": {"$ref": "#/components/schemas/SettingsProblem"}}}


@router.get("/runners", response_model=HostRunnerListResponse)
def list_runners(request: Request) -> HostRunnerListResponse:
    """Every runner the host holds — a served one with its brake, bindings and agents in use, a halted one with its
    reasons, one whose latest start failed with its step and error, and one added and not started yet. A served
    runner its identity verdict refuses — its token refused or naming another runner, or its hub not holding it under
    the host — is halted too, naming why."""
    wiring = RunnerWiring.of(request)
    registry = wiring.runners()
    items = [_served_view(wiring, served) for served in registry.served]
    failures = wiring.maybe_start_failures()
    for halted in registry.halted:
        failure = failures.of(halted.id) if failures is not None and halted.id is not None else None
        items.append(
            HostRunnerView(
                runner_id=halted.id,
                name=halted.name,
                state="start_failed" if failure is not None else "halted",
                halted_reasons=list(halted.errors),
                start_failure=(
                    HostRunnerStartFailureView(step=failure.step, error=failure.error) if failure is not None else None
                ),
            )
        )
    # Only a host whose tick reconciles its hosted runners starts one the store holds and it does not run yet.
    if failures is not None:
        listed = {item.runner_id for item in items}
        items.extend(
            HostRunnerView(runner_id=runner_id, name=name, state="starting")
            for runner_id, name in _active(wiring)
            if runner_id not in listed
        )
    return HostRunnerListResponse(items=items)


def held_runner_ids(wiring: RunnerWiring) -> frozenset[str]:
    """Every runner the host holds: served, halted, or hosted in its store and not started yet."""
    registry = wiring.runners()
    held = {served.runner_id for served in registry.served}
    held.update(halted.id for halted in registry.halted if halted.id is not None)
    held.update(runner_id for runner_id, _ in _active(wiring))
    return frozenset(held)


def _active(wiring: RunnerWiring) -> tuple[tuple[str, str], ...]:
    """Each active hosted runner, (id, name)."""
    service = wiring.maybe_hosted_runners()
    if service is not None:
        return service.active()
    stores = wiring.maybe_host_read_stores()
    return tuple((runner_id, runner_id) for runner_id in stores.hosted_runners.active()) if stores is not None else ()


def _served_view(wiring: RunnerWiring, served: ServedRunner) -> HostRunnerView:
    runner = wiring.for_runner(served)
    config = runner.config()
    bindings = [
        HostRunnerBindingView(project=b.project, workspace_id=b.workspace, agents=config.agents)
        for b in config.bindings or ()
    ]
    current = runner.maybe_identity_verdict()
    state, halted_reasons = served_runner_state(current.verdict() if current is not None else None)
    service = runner.maybe_status()
    if service is None:
        # A store-free runner: listed, with nothing of its brake or capacity to read.
        return HostRunnerView(
            runner_id=served.runner_id,
            name=served.name,
            state=state,
            served=True,
            halted_reasons=halted_reasons,
            bindings=bindings,
        )
    summary = service.summary()
    return HostRunnerView(
        runner_id=served.runner_id,
        name=served.name,
        state=state,
        served=True,
        halted_reasons=halted_reasons,
        pause=PauseStateView(
            local=summary.pause.local,
            hub=summary.pause.hub,
            effective=summary.pause.effective,
            local_reason=summary.pause.local_reason,
        ),
        throttle=throttle_view(summary.throttle) if summary.throttle is not None else None,
        bindings=[
            HostRunnerBindingView(project=b.project, workspace_id=b.workspace_id, agents=b.agents)
            for b in summary.capacities.bindings
        ],
        agents_in_use=summary.capacities.used,
        max_agents=summary.capacities.agents,
    )


@writes.post(
    "/runners",
    response_model=HostRunnerView,
    responses={
        403: {"description": "Superuser required"},
        409: {"description": "The host holds no host token, or its hub refused the add", "content": _PROBLEM},
        422: {"description": "The runner's settings would not resolve", "content": _SETTINGS_PROBLEM},
        503: {"description": "The hub could not be reached", "content": _PROBLEM},
    },
)
def add_runner(
    request: Request,
    body: HostRunnerAddRequest,
    session: Annotated[RunnerSession, Depends(require_superuser)],
) -> HostRunnerView | JSONResponse:
    """Add a runner to this host: its settings judged as a write of them is, then minted at the hub under the host's
    token and written to the store in one transaction. The host starts it at its next tick."""
    wiring = RunnerWiring.of(request)
    service = wiring.maybe_hosted_runners()
    if service is None:
        return problem(409, "host_unregistered", "this host holds no host token, so it cannot add runners at its hub")
    new = NewRunner.of(
        body.name,
        [(project.project, project.workspace) for project in body.projects],
        body.settings,
        workspaces=[workspace.workspace_id for workspace in wiring.workspaces()],
    )
    try:
        added = service.add(new, actor=session.username, door=API)
    except RunnerSettingsInvalid as exc:
        return settings_problem(
            422,
            "settings_invalid",
            str(exc),
            errors=[{"key": key, "message": message} for key, message in exc.problems.items()],
        )
    except HostRunnerHubRefused as exc:
        return problem(409, "hub_refused", f"the hub refused this host's token: {exc.refused.detail}")
    except RunnerAddRefused as exc:
        return problem(409, "hub_refused", f"the hub refused to add runner {body.name}: {exc.detail}")
    except HubClientError as exc:
        return problem(503, "hub_unreachable", f"cannot add runner {body.name} at the hub: {exc}")
    return HostRunnerView(runner_id=added.runner_id, name=added.name, state="starting")


@writes.post(
    "/runners/{runner_id}/retire",
    response_model=HostRunnerRetiredView,
    responses={
        403: {"description": "Superuser required"},
        404: {"description": "No runner of this host has the id", "content": _PROBLEM},
        409: {"description": "The hub refused the retire", "content": _PROBLEM},
        503: {"description": "The hub could not be reached", "content": _PROBLEM},
    },
)
def retire_runner(
    request: Request,
    runner_id: str,
    body: HostRunnerRetireRequest,
    session: Annotated[RunnerSession, Depends(require_superuser)],
) -> HostRunnerRetiredView | JSONResponse:
    """Retire one of this host's runners at its hub — with ``force``, releasing the chunks it holds — then record it
    retired; the host drains it at its next tick. Retirement is final: a retired runner's retire writes nothing."""
    wiring = RunnerWiring.of(request)
    service = wiring.maybe_hosted_runners()
    if service is None:
        if runner_id not in held_runner_ids(wiring):
            return problem(404, "runner_unknown", f"this host holds no runner {runner_id}")
        return problem(
            409, "host_unregistered", "this host holds no host token, so it cannot retire runners at its hub"
        )
    runner = service.held(runner_id)
    if runner is None:
        return problem(404, "runner_unknown", f"this host holds no runner {runner_id}")
    try:
        retired = service.retire(runner, force=body.force, actor=session.username, door=API)
    except HostRunnerHubRefused as exc:
        return problem(409, "hub_refused", f"the hub refused to retire runner {runner_id}: {exc.refused.detail}")
    except HostRunnerRetireRefused as exc:
        return problem(409, "hub_refused", f"the hub refused to retire runner {runner_id}: {exc.detail}")
    except HubClientError as exc:
        return problem(503, "hub_unreachable", f"cannot retire runner {runner_id} at the hub: {exc}")
    return HostRunnerRetiredView(
        runner_id=retired.runner_id,
        name=retired.name,
        state="retired",
        released_chunk_ids=list(retired.released_chunk_ids),
        already=retired.already,
    )
