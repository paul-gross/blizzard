"""The host's own reads — ``GET /api/host/registration`` and ``/environments``; ``/runners`` is
``src/blizzard/runner/api/host_runners.py``'s, ``/workspaces`` and ``/subscriptions``
``src/blizzard/runner/api/host_records.py``'s.

Each composes the registered runners' own bound seams, stamping each runner's id; none reads across runners
(``bzh:runner-scoped-store``). Read-only over their wiring (``bzh:controller-read-only``)."""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, Request

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.api.wiring import RunnerWiring, ServedWorkspace
from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.environments.workspaces import held_workspace_id
from blizzard.wire.runner_host import (
    HostEnvironmentListResponse,
    HostEnvironmentView,
    HostRegistrationView,
    HostWorkspaceEnvironmentsView,
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
