"""The host's own reads — ``GET /api/host/registration``, ``/workspaces``, ``/environments`` and ``/subscriptions``;
``/runners`` is ``src/blizzard/runner/api/host_runners.py``'s.

Each composes the registered runners' own bound seams, stamping each runner's id; none reads across runners
(``bzh:runner-scoped-store``). Read-only over their wiring (``bzh:controller-read-only``)."""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, Request

from blizzard.foundation.store.utc import iso_utc
from blizzard.foundation.subscription_miss import SampleMissReason
from blizzard.runner.api.wiring import RunnerWiring, ServedWorkspace
from blizzard.runner.config import SubscriptionDeclaration
from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.environments.workspaces import held_workspace_id
from blizzard.runner.subscriptions.subscription_sampler import ExternalSubscriptionUsageWindow
from blizzard.runner.usage.repository import IReadExternalUsageRepository
from blizzard.wire.runner import ExternalSubscriptionUsageWindowView
from blizzard.wire.runner_host import (
    HostEnvironmentListResponse,
    HostEnvironmentView,
    HostRegistrationView,
    HostSubscriptionListResponse,
    HostSubscriptionView,
    HostWorkspaceEnvironmentsView,
    HostWorkspaceListResponse,
    HostWorkspaceView,
)

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
    return subscription_list(wiring.subscriptions(), wiring.host_read_stores().external_usage, spenders)


def subscription_list(
    declarations: Sequence[SubscriptionDeclaration],
    usage: IReadExternalUsageRepository,
    spenders: dict[str, list[str]],
) -> HostSubscriptionListResponse:
    slugs = [d.slug for d in declarations]
    attempts = usage.latest_external_usage_attempts_by_slug(slugs)
    renewals = usage.latest_credential_renewals_by_slug(slugs)
    samples = usage.latest_external_usage_samples_by_slug(slugs)
    items: list[HostSubscriptionView] = []
    for declaration in declarations:
        attempt = attempts.get(declaration.slug)
        renewal = renewals.get(declaration.slug)
        sample = samples.get(declaration.slug)
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
                windows=[_window_view(w) for w in sample.windows] if sample is not None else [],
                windows_sampled_at=iso_utc(sample.sampled_at) if sample is not None else None,
                condition=attempt.condition() if attempt is not None else None,
            )
        )
    return HostSubscriptionListResponse(items=items)


def _window_view(window: ExternalSubscriptionUsageWindow) -> ExternalSubscriptionUsageWindowView:
    return ExternalSubscriptionUsageWindowView(
        window=window.window,
        utilization_pct=window.utilization_pct,
        resets_at=iso_utc(window.resets_at),
        window_seconds=window.window_seconds,
    )
