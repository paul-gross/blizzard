"""The host's own reads — ``GET /api/host/registration``, ``/runners``, ``/workspaces``, ``/environments`` and
``/subscriptions``.

Each composes the registered runners' own bound seams, stamping each runner's id; none reads across runners
(``bzh:runner-scoped-store``). Read-only over their wiring (``bzh:controller-read-only``)."""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, Request

from blizzard.foundation.runner_identity_state import IdentityState
from blizzard.foundation.store.utc import iso_utc
from blizzard.foundation.subscription_miss import SampleMissReason
from blizzard.runner.api.wiring import RunnerWiring, ServedWorkspace
from blizzard.runner.config import SubscriptionDeclaration
from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.environments.workspaces import held_workspace_id
from blizzard.runner.hub.identity import ICurrentIdentityVerdict
from blizzard.runner.usage.repository import IReadExternalUsageRepository
from blizzard.wire.runner_host import (
    HostEnvironmentListResponse,
    HostEnvironmentView,
    HostRegistrationView,
    HostRunnerBindingView,
    HostRunnerListResponse,
    HostRunnerState,
    HostRunnerView,
    HostSubscriptionListResponse,
    HostSubscriptionView,
    HostWorkspaceEnvironmentsView,
    HostWorkspaceListResponse,
    HostWorkspaceView,
)
from blizzard.wire.runner_status import PauseStateView

router = APIRouter(prefix="/api/host", tags=["host"])


@router.get("/registration", response_model=HostRegistrationView)
def host_registration(request: Request) -> HostRegistrationView:
    """Where the host stands with its hub: unregistered, or — holding a host token — what its latest report found,
    with the identity its first answered report recorded and the facts waiting on its own lane."""
    wiring = RunnerWiring.of(request)
    status = wiring.host_registration().current()
    identity = wiring.host_identity().current()
    stores = wiring.maybe_host_read_stores()
    return HostRegistrationView(
        state=status.state,
        host_id=identity.host_id if identity is not None else None,
        tenant_id=identity.tenant_id if identity is not None else None,
        host_name=status.host_name,
        answered_at=iso_utc(status.answered_at) if status.answered_at is not None else None,
        held_runner_ids=list(status.held_runner_ids),
        detail=status.detail,
        buffered_facts=stores.host_lane.host_buffered_count() if stores is not None else 0,
    )


@router.get("/runners", response_model=HostRunnerListResponse)
def list_runners(request: Request) -> HostRunnerListResponse:
    """Every runner the host holds — a served one with its brake, bindings and agents in use, a halted one with its
    reasons. A served runner its identity verdict refuses — its token refused or naming another runner, or its hub
    not holding it under the host — is halted too, naming why."""
    wiring = RunnerWiring.of(request)
    registry = wiring.runners()
    items: list[HostRunnerView] = []
    for served in registry.served:
        runner = wiring.for_runner(served)
        bindings = [
            HostRunnerBindingView(project=b.project, workspace_id=b.workspace, agents=b.agents)
            for b in runner.config().bindings or ()
        ]
        halted_reasons = _refusal(runner.maybe_identity_verdict())
        state: HostRunnerState = "halted" if halted_reasons else "serving"
        service = runner.maybe_status()
        if service is None:
            # A store-free runner: listed, with nothing of its brake or capacity to read.
            items.append(
                HostRunnerView(
                    runner_id=served.runner_id,
                    name=served.name,
                    state=state,
                    served=True,
                    halted_reasons=halted_reasons,
                    bindings=bindings,
                )
            )
            continue
        summary = service.summary()
        items.append(
            HostRunnerView(
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
                bindings=bindings,
                agents_in_use=summary.capacities.used,
                max_agents=sum(b.agents for b in summary.capacities.bindings),
            )
        )
    items.extend(
        HostRunnerView(runner_id=halted.id, name=halted.name, state="halted", halted_reasons=list(halted.errors))
        for halted in registry.halted
    )
    return HostRunnerListResponse(items=items)


def _refusal(identity: ICurrentIdentityVerdict | None) -> list[str]:
    """Why a served runner's identity verdict halts it, or nothing while it does not."""
    if identity is None:
        return []
    verdict = identity.verdict()
    if not verdict.refusing():
        return []
    if verdict.state is IdentityState.MISMATCHED:
        return [f"its token belongs to runner {verdict.token_runner_id}, not the configured {verdict.store_runner_id}"]
    return [verdict.reason or str(verdict.state)]


@router.get("/workspaces", response_model=HostWorkspaceListResponse)
def list_workspaces(request: Request) -> HostWorkspaceListResponse:
    """Every workspace the host declares."""
    return HostWorkspaceListResponse(
        items=[
            HostWorkspaceView(workspace_id=w.workspace_id, max_environments=w.max_environments)
            for w in RunnerWiring.of(request).workspaces()
        ]
    )


@router.get("/environments", response_model=HostEnvironmentListResponse)
def list_environments(request: Request) -> HostEnvironmentListResponse:
    """Every workspace's environments, held or free; a held one names its runner and its chunk's project."""
    wiring = RunnerWiring.of(request)
    declared = tuple(workspace.workspace_id for workspace in wiring.workspaces())
    held: list[tuple[str, str, EnvBinding]] = []
    for served in wiring.runners().served:
        runner = wiring.for_runner(served)
        bound = runner.config().bound_workspace_ids
        held.extend(
            (served.runner_id, held_workspace_id(binding.workspace_id, bound, declared), binding)
            for binding in runner.read_stores().environments.held_bindings()
        )
    return HostEnvironmentListResponse(
        workspaces=[
            HostWorkspaceEnvironmentsView(
                workspace_id=workspace.workspace_id,
                items=_workspace_environments(
                    workspace, [(rid, b) for rid, wid, b in held if wid == workspace.workspace_id]
                ),
            )
            for workspace in wiring.workspaces()
        ]
    )


def _workspace_environments(
    workspace: ServedWorkspace, held: Sequence[tuple[str, EnvBinding]]
) -> list[HostEnvironmentView]:
    """The pool in order, each slot joined against the binding holding it; a binding off the pool, or a second one on
    a slot, gets its own row, so a held environment never vanishes."""
    by_env: dict[str, list[tuple[str, EnvBinding]]] = {}
    for holder in held:
        by_env.setdefault(holder[1].environment_id, []).append(holder)
    items: list[HostEnvironmentView] = []
    for env_id in workspace.env_pool:
        holders = by_env.pop(env_id, [])
        if not holders:
            items.append(HostEnvironmentView(environment_id=env_id))
        items.extend(_held_view(runner_id, binding) for runner_id, binding in holders)
    for holders in by_env.values():
        items.extend(_held_view(runner_id, binding) for runner_id, binding in holders)
    return items


def _held_view(runner_id: str, binding: EnvBinding) -> HostEnvironmentView:
    return HostEnvironmentView(
        environment_id=binding.environment_id,
        chunk_id=binding.chunk_id,
        held_since=iso_utc(binding.bound_at),
        held=True,
        runner_id=runner_id,
        project=binding.project_id,
    )


@router.get("/subscriptions", response_model=HostSubscriptionListResponse)
def list_subscriptions(request: Request) -> HostSubscriptionListResponse:
    """Every declared subscription's own newest sampling attempt and credential renewal, with the runners that spend
    it."""
    wiring = RunnerWiring.of(request)
    spenders: dict[str, list[str]] = {}
    for served in wiring.runners().served:
        for declaration in wiring.for_runner(served).config().resolved_subscriptions():
            spenders.setdefault(declaration.slug, []).append(served.runner_id)
    return _subscription_list(wiring.subscriptions(), wiring.host_read_stores().external_usage, spenders)


def _subscription_list(
    declarations: Sequence[SubscriptionDeclaration],
    usage: IReadExternalUsageRepository,
    spenders: dict[str, list[str]],
) -> HostSubscriptionListResponse:
    slugs = [d.slug for d in declarations]
    attempts = usage.latest_external_usage_attempts_by_slug(slugs)
    renewals = usage.latest_credential_renewals_by_slug(slugs)
    items: list[HostSubscriptionView] = []
    for declaration in declarations:
        attempt = attempts.get(declaration.slug)
        renewal = renewals.get(declaration.slug)
        items.append(
            HostSubscriptionView(
                slug=declaration.slug,
                name=declaration.name,
                provider=declaration.provider,
                sampled_at=iso_utc(attempt.sampled_at) if attempt is not None else None,
                ok=attempt.ok if attempt is not None else None,
                miss_reason=SampleMissReason.recognized(attempt.miss_reason) if attempt is not None else None,
                renewal_attempted_at=iso_utc(renewal.attempted_at) if renewal is not None else None,
                renewal_result=renewal.result if renewal is not None else None,
                renewal_failure_reason=renewal.failure_reason if renewal is not None else None,
                spenders=spenders.get(declaration.slug, []),
            )
        )
    return HostSubscriptionListResponse(items=items)
