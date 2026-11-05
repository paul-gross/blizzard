"""Helpers for the route classification tests, shared with other test modules."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

from blizzard.hub.auth.permissions import Permission


@runtime_checkable
class _IncludeContext(Protocol):
    prefix: str


@runtime_checkable
class _MountedRouter(Protocol):
    """An included router as FastAPI mounts it, matched on its public ``original_router`` and ``include_context``."""

    original_router: APIRouter
    include_context: _IncludeContext


#: The human routes that serve no tenant, gated by ``require_hub(<permission>)``: every other human
#: route, and every fleet route, resolves its tenant at router level.
HUB_LEVEL: set[tuple[str, str]] = {
    ("POST", "/api/auth/rotate-signing-key"),
    ("GET", "/api/traces/status"),
    ("POST", "/api/traces/replay"),
    ("GET", "/api/egress/status"),
    ("POST", "/api/egress/reset"),
    ("POST", "/api/egress/backfill"),
    ("GET", "/api/admin/hub-admins"),
    ("PUT", "/api/admin/hub-admins/{user_id}"),
    ("DELETE", "/api/admin/hub-admins/{user_id}"),
    ("POST", "/api/admin/tenants"),
    ("GET", "/api/admin/tenants"),
    ("PATCH", "/api/admin/tenants/{tenant_id}"),
    ("DELETE", "/api/admin/tenants/{tenant_id}"),
    ("POST", "/api/admin/graphs/sync"),
    ("GET", "/api/admin/events"),
    ("GET", "/api/admin/invariants"),
    ("POST", "/api/admin/sweeps/{name}/passes"),
}


#: Human plane — ``(method, path) -> permission`` required via ``require(<permission>)``, or
#: ``require_hub(<permission>)`` for a route in :data:`_HUB_LEVEL`.
HUMAN: dict[tuple[str, str], Permission] = {
    ("GET", "/api/events/stream"): Permission.FLEET_VIEW,
    ("GET", "/api/events"): Permission.FLEET_VIEW,
    ("GET", "/api/activity"): Permission.FLEET_VIEW,
    ("POST", "/api/graphs"): Permission.GRAPH_EDIT,
    # Reconciliation mints, so it needs exactly what an explicit mint needs.
    ("POST", "/api/graphs/sync"): Permission.GRAPH_EDIT,
    ("GET", "/api/graphs"): Permission.FLEET_VIEW,
    ("GET", "/api/graphs/{graph_id}"): Permission.FLEET_VIEW,
    ("POST", "/api/graphs/{graph_id}/retire"): Permission.GRAPH_EDIT,
    ("POST", "/api/graphs/{graph_id}/enable"): Permission.GRAPH_EDIT,
    # The graph's mutable flags, and the deprecated follow-latest alias onto the same verb.
    ("PATCH", "/api/graphs/{graph_id}"): Permission.GRAPH_EDIT,
    ("POST", "/api/graphs/{graph_id}/follow-latest"): Permission.GRAPH_EDIT,
    # Scopes — reads take Permission.FLEET_VIEW, writes take Permission.GRAPH_EDIT.
    ("POST", "/api/scopes"): Permission.GRAPH_EDIT,
    ("GET", "/api/scopes"): Permission.FLEET_VIEW,
    ("GET", "/api/scopes/{slug}"): Permission.FLEET_VIEW,
    ("GET", "/api/scopes/{slug}/routines"): Permission.FLEET_VIEW,
    ("PATCH", "/api/scopes/{slug}"): Permission.GRAPH_EDIT,
    ("POST", "/api/scopes/{slug}/retire"): Permission.GRAPH_EDIT,
    ("POST", "/api/scopes/{slug}/enable"): Permission.GRAPH_EDIT,
    ("POST", "/api/secrets"): Permission.CONFIG_EDIT,
    ("GET", "/api/secrets"): Permission.FLEET_VIEW,
    ("GET", "/api/secrets/{name}"): Permission.FLEET_VIEW,
    ("PUT", "/api/secrets/{name}/value"): Permission.CONFIG_EDIT,
    ("POST", "/api/secrets/{name}/retire"): Permission.CONFIG_EDIT,
    ("POST", "/api/secrets/{name}/enable"): Permission.CONFIG_EDIT,
    # Routines — reads take Permission.FLEET_VIEW, writes take Permission.GRAPH_EDIT.
    ("POST", "/api/routines"): Permission.GRAPH_EDIT,
    ("GET", "/api/routines"): Permission.FLEET_VIEW,
    ("GET", "/api/routines/{routine_id}"): Permission.FLEET_VIEW,
    ("PATCH", "/api/routines/{routine_id}"): Permission.GRAPH_EDIT,
    ("POST", "/api/routines/{routine_id}/retire"): Permission.GRAPH_EDIT,
    ("POST", "/api/routines/{routine_id}/enable"): Permission.GRAPH_EDIT,
    ("GET", "/api/routines/trend"): Permission.FLEET_VIEW,  #
    ("GET", "/api/routines/proposal-counts"): Permission.FLEET_VIEW,  #
    ("GET", "/api/routines/{routine_id}/sweeps"): Permission.FLEET_VIEW,
    # A routine's scope membership — reads take Permission.FLEET_VIEW, writes take
    # Permission.GRAPH_EDIT, the same split as scopes/routines themselves.
    ("GET", "/api/routines/{routine_id}/scopes"): Permission.FLEET_VIEW,
    ("PUT", "/api/routines/{routine_id}/scopes/{scope_slug}"): Permission.GRAPH_EDIT,
    ("DELETE", "/api/routines/{routine_id}/scopes/{scope_slug}"): Permission.GRAPH_EDIT,
    # Mint and ingest a run in one act — the same Permission.CHUNK_CONTROL
    # the act it composes (ingest) already requires.
    ("POST", "/api/routines/{routine_id}/run"): Permission.CHUNK_CONTROL,
    # The per-scope delta baseline a routine has swept — a read, Permission.FLEET_VIEW.
    ("GET", "/api/routines/{routine_id}/baselines"): Permission.FLEET_VIEW,
    # The run list and one run's own delta — reads, Permission.FLEET_VIEW.
    ("GET", "/api/runs"): Permission.FLEET_VIEW,
    ("GET", "/api/runs/{chunk_id}"): Permission.FLEET_VIEW,
    # Findings and garden proposals — read-only routes, both Permission.FLEET_VIEW.
    ("GET", "/api/findings"): Permission.FLEET_VIEW,
    ("GET", "/api/findings/classes"): Permission.FLEET_VIEW,
    ("GET", "/api/findings/{finding_id}"): Permission.FLEET_VIEW,
    ("GET", "/api/garden-proposals"): Permission.FLEET_VIEW,
    ("GET", "/api/garden-proposals/{proposal_id}"): Permission.FLEET_VIEW,
    # Closing a garden proposal — Permission.CHUNK_CONTROL, the same permission a
    # not-chunk-scoped work-item write already carries.
    ("POST", "/api/garden-proposals/{proposal_id}/pass"): Permission.CHUNK_CONTROL,
    ("POST", "/api/garden-proposals/{proposal_id}/accept"): Permission.CHUNK_CONTROL,
    # Operator authoring — create/edit/attach/detach — the same
    # Permission.CHUNK_CONTROL pass/accept already carry.
    ("POST", "/api/garden-proposals"): Permission.CHUNK_CONTROL,
    ("PATCH", "/api/garden-proposals/{proposal_id}"): Permission.CHUNK_CONTROL,
    ("POST", "/api/garden-proposals/{proposal_id}/attach"): Permission.CHUNK_CONTROL,
    ("POST", "/api/garden-proposals/{proposal_id}/detach"): Permission.CHUNK_CONTROL,
    # The human-driven exit verbs and `reopen` over findings — the
    # same Permission.CHUNK_CONTROL a garden-proposal closure already carries.
    ("POST", "/api/findings/resolve"): Permission.CHUNK_CONTROL,
    ("POST", "/api/findings/confirm-gone"): Permission.CHUNK_CONTROL,
    ("POST", "/api/findings/wont-fix"): Permission.CHUNK_CONTROL,
    ("POST", "/api/findings/not-a-finding"): Permission.CHUNK_CONTROL,
    ("POST", "/api/findings/supersede"): Permission.CHUNK_CONTROL,
    ("POST", "/api/findings/reopen"): Permission.CHUNK_CONTROL,
    ("POST", "/api/chunks"): Permission.CHUNK_INGEST,
    ("GET", "/api/chunks"): Permission.FLEET_VIEW,
    ("GET", "/api/chunk-counts"): Permission.FLEET_VIEW,
    ("GET", "/api/chunks/{chunk_id}"): Permission.FLEET_VIEW,
    ("GET", "/api/flows"): Permission.FLEET_VIEW,
    ("GET", "/api/flows/{flow_id}"): Permission.FLEET_VIEW,
    ("GET", "/api/flows/{flow_id}/record"): Permission.FLEET_VIEW,
    ("POST", "/api/chunks/{chunk_id}/requeues"): Permission.CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/restart"): Permission.CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/detach"): Permission.CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/pause"): Permission.CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/resume"): Permission.CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/stop"): Permission.CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/complete"): Permission.CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/promote"): Permission.CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/demote"): Permission.CHUNK_CONTROL,
    # Declare/release a dependency edge — the same Permission.CHUNK_CONTROL every
    # other chunk-control-plane verb here already carries.
    ("POST", "/api/chunks/{chunk_id}/dependencies"): Permission.CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/dependencies/release"): Permission.CHUNK_CONTROL,
    ("PATCH", "/api/chunks/{chunk_id}"): Permission.CHUNK_CONTROL,
    ("DELETE", "/api/chunks/{chunk_id}"): Permission.CHUNK_CONTROL,
    ("GET", "/api/chunks/{chunk_id}/work-items"): Permission.FLEET_VIEW,
    ("GET", "/api/work-items"): Permission.FLEET_VIEW,
    # The issue-#55 deprecated alias onto the same handler — classified identically,
    # because an alias that fell into a different plane would be an authz hole.
    ("GET", "/api/chunks/{chunk_id}/pm-items"): Permission.FLEET_VIEW,
    ("GET", "/api/decisions"): Permission.FLEET_VIEW,
    ("POST", "/api/decisions/{decision_id}/resolutions"): Permission.GATE_RESOLVE,
    ("GET", "/api/queue"): Permission.FLEET_VIEW,
    ("PUT", "/api/queue"): Permission.QUEUE_REORDER,
    ("POST", "/api/queue/position"): Permission.QUEUE_REORDER,
    # Backlog (``not_ready``) reorder — Permission.QUEUE_REORDER even to read, an operator
    # triage surface, narrower than the ready queue's Permission.FLEET_VIEW.
    ("GET", "/api/backlog"): Permission.QUEUE_REORDER,
    ("PUT", "/api/backlog"): Permission.QUEUE_REORDER,
    ("POST", "/api/backlog/position"): Permission.QUEUE_REORDER,
    ("POST", "/api/chunks/{chunk_id}/group"): Permission.QUEUE_REORDER,
    ("POST", "/api/questions"): Permission.QUESTION_ANSWER,
    ("POST", "/api/questions/{question_id}/answers"): Permission.QUESTION_ANSWER,
    ("GET", "/api/questions"): Permission.FLEET_VIEW,
    # Adding a runner and rotating its token both mint a credential.
    ("POST", "/api/runners"): Permission.RUNNER_ADD,
    ("POST", "/api/runners/{runner_id}/enrollments"): Permission.RUNNER_ADD,
    ("GET", "/api/runners"): Permission.FLEET_VIEW,
    ("GET", "/api/runners/{runner_id}"): Permission.FLEET_VIEW,
    ("POST", "/api/runners/{runner_id}/pause"): Permission.RUNNER_PAUSE,
    ("POST", "/api/runners/{runner_id}/resume"): Permission.RUNNER_PAUSE,
    ("POST", "/api/runners/{runner_id}/retire"): Permission.RUNNER_RETIRE,
    ("POST", "/api/runners/{runner_id}/reinstate"): Permission.RUNNER_RETIRE,
    ("POST", "/api/runners/{runner_id}/token-revocations"): Permission.RUNNER_RETIRE,
    ("POST", "/api/hosts"): Permission.RUNNER_RETIRE,
    ("GET", "/api/hosts"): Permission.FLEET_VIEW,
    ("GET", "/api/hosts/{host_id}"): Permission.FLEET_VIEW,
    ("POST", "/api/hosts/{host_id}/adoptions"): Permission.RUNNER_RETIRE,
    ("POST", "/api/hosts/{host_id}/releases"): Permission.RUNNER_RETIRE,
    ("POST", "/api/hosts/{host_id}/enrollments"): Permission.RUNNER_RETIRE,
    ("POST", "/api/hosts/{host_id}/token-revocations"): Permission.RUNNER_RETIRE,
    ("POST", "/api/hosts/{host_id}/retire"): Permission.RUNNER_RETIRE,
    ("GET", "/api/spend"): Permission.FLEET_VIEW,
    ("GET", "/api/users"): Permission.USER_MANAGE,
    ("POST", "/api/users/{user_id}/role"): Permission.USER_MANAGE,
    ("GET", "/api/members"): Permission.USER_MANAGE,
    ("PUT", "/api/members/{user_id}"): Permission.USER_MANAGE,
    ("DELETE", "/api/members/{user_id}"): Permission.USER_MANAGE,
    # Only a hub administrator makes another.
    ("GET", "/api/admin/hub-admins"): Permission.HUB_ADMIN_GRANT,
    ("PUT", "/api/admin/hub-admins/{user_id}"): Permission.HUB_ADMIN_GRANT,
    ("DELETE", "/api/admin/hub-admins/{user_id}"): Permission.HUB_ADMIN_GRANT,
    ("POST", "/api/invitations"): Permission.USER_MANAGE,
    ("GET", "/api/invitations"): Permission.USER_MANAGE,
    ("POST", "/api/invitations/{invitation_id}/revocations"): Permission.USER_MANAGE,
    # Key rotation — the key signs runner sign-ins in every tenant, so it takes a hub permission.
    ("POST", "/api/auth/rotate-signing-key"): Permission.HUB_ADMIN_GRANT,
    ("GET", "/api/chunks/{chunk_id}/flow-submissions"): Permission.FLEET_VIEW,
    ("GET", "/api/chunks/{chunk_id}/flow-submissions/{submission_id}/turns"): Permission.FLEET_VIEW,
    ("GET", "/api/chunks/{chunk_id}/landing-facts"): Permission.FLEET_VIEW,
    ("GET", "/api/chunks/{chunk_id}/close-diagnostic"): Permission.FLEET_VIEW,
    # Transcript-segment discovery/content reads — above
    # Permission.FLEET_VIEW, since a transcript carries everything a worker saw.
    ("GET", "/api/chunks/{chunk_id}/transcripts"): Permission.TRANSCRIPT_READ,
    ("GET", "/api/chunks/{chunk_id}/transcripts/{segment_id}"): Permission.TRANSCRIPT_READ,
    # Forced transcript-event re-derivation — a mutation, above the
    # read-only Permission.TRANSCRIPT_READ.
    ("POST", "/api/analytics/re-derive"): Permission.ANALYTICS_ADMIN,
    ("POST", "/api/sweeps/{name}/passes"): Permission.ANALYTICS_ADMIN,
    # The export surfaces — their cursors and directories are shared by every tenant, so each
    # takes the hub's export permission.
    ("GET", "/api/traces/status"): Permission.EXPORT_ADMIN,
    ("POST", "/api/traces/replay"): Permission.EXPORT_ADMIN,
    ("GET", "/api/egress/status"): Permission.EXPORT_ADMIN,
    ("POST", "/api/egress/reset"): Permission.EXPORT_ADMIN,
    ("POST", "/api/egress/backfill"): Permission.EXPORT_ADMIN,
    # Tenant administration and the hub's own event log — hub administrators alone.
    ("POST", "/api/admin/tenants"): Permission.TENANT_ADMIN,
    ("GET", "/api/admin/tenants"): Permission.TENANT_ADMIN,
    ("PATCH", "/api/admin/tenants/{tenant_id}"): Permission.TENANT_ADMIN,
    ("DELETE", "/api/admin/tenants/{tenant_id}"): Permission.TENANT_ADMIN,
    ("POST", "/api/admin/graphs/sync"): Permission.TENANT_ADMIN,
    ("GET", "/api/admin/events"): Permission.TENANT_ADMIN,
    ("GET", "/api/admin/invariants"): Permission.TENANT_ADMIN,
    ("POST", "/api/admin/sweeps/{name}/passes"): Permission.TENANT_ADMIN,
    # The read-only events/counts surfaces — no grant of their own.
    ("GET", "/api/analytics/events"): Permission.TRANSCRIPT_READ,
    ("GET", "/api/analytics/events/ndjson"): Permission.TRANSCRIPT_READ,
    ("GET", "/api/analytics/counts/files"): Permission.TRANSCRIPT_READ,
    ("GET", "/api/analytics/counts/skills"): Permission.TRANSCRIPT_READ,
    ("GET", "/api/analytics/counts/agent-types"): Permission.TRANSCRIPT_READ,
    ("GET", "/api/analytics/counts/nodes"): Permission.TRANSCRIPT_READ,
    # The operational datasets — durations, spend, outcomes — no
    # grant of their own, strictly narrower than the Permission.FLEET_VIEW the same numbers
    # already sit behind at /api/spend and on every board card.
    ("GET", "/api/analytics/durations/nodes"): Permission.TRANSCRIPT_READ,
    ("GET", "/api/analytics/durations/graphs"): Permission.TRANSCRIPT_READ,
    ("GET", "/api/analytics/spend/nodes"): Permission.TRANSCRIPT_READ,
    ("GET", "/api/analytics/spend/graphs"): Permission.TRANSCRIPT_READ,
    ("GET", "/api/analytics/spend/models"): Permission.TRANSCRIPT_READ,
    ("GET", "/api/analytics/spend/chunks"): Permission.TRANSCRIPT_READ,
    ("GET", "/api/analytics/spend/chunks/ndjson"): Permission.TRANSCRIPT_READ,
    ("GET", "/api/analytics/outcomes/nodes"): Permission.TRANSCRIPT_READ,
    # The work-source item routes — the same two permissions the chunk
    # work-item read and its mutations already sit behind.
    ("GET", "/api/work-sources"): Permission.FLEET_VIEW,
    ("POST", "/api/work-sources"): Permission.CONFIG_EDIT,
    ("GET", "/api/work-sources/{source}"): Permission.FLEET_VIEW,
    ("PATCH", "/api/work-sources/{source}"): Permission.CONFIG_EDIT,
    ("POST", "/api/work-sources/{source}/retire"): Permission.CONFIG_EDIT,
    ("POST", "/api/work-sources/{source}/enable"): Permission.CONFIG_EDIT,
    ("GET", "/api/repositories"): Permission.FLEET_VIEW,
    ("GET", "/api/forges"): Permission.FLEET_VIEW,
    ("POST", "/api/forges"): Permission.CONFIG_EDIT,
    ("GET", "/api/forges/{forge}"): Permission.FLEET_VIEW,
    ("PATCH", "/api/forges/{forge}"): Permission.CONFIG_EDIT,
    ("POST", "/api/forges/{forge}/retire"): Permission.CONFIG_EDIT,
    ("POST", "/api/forges/{forge}/enable"): Permission.CONFIG_EDIT,
    ("POST", "/api/repositories"): Permission.CONFIG_EDIT,
    ("GET", "/api/repositories/{repository}"): Permission.FLEET_VIEW,
    ("PATCH", "/api/repositories/{repository}"): Permission.CONFIG_EDIT,
    ("POST", "/api/repositories/{repository}/retire"): Permission.CONFIG_EDIT,
    ("POST", "/api/repositories/{repository}/enable"): Permission.CONFIG_EDIT,
    ("GET", "/api/projects"): Permission.FLEET_VIEW,
    ("POST", "/api/projects"): Permission.CONFIG_EDIT,
    ("GET", "/api/projects/{project}"): Permission.FLEET_VIEW,
    ("PATCH", "/api/projects/{project}"): Permission.CONFIG_EDIT,
    ("GET", "/api/projects/{project}/source-links"): Permission.FLEET_VIEW,
    ("PUT", "/api/projects/{project}/source-links/{source}"): Permission.CONFIG_EDIT,
    ("POST", "/api/projects/{project}/source-links/{source}/retire"): Permission.CONFIG_EDIT,
    ("GET", "/api/projects/{project}/repository-links"): Permission.FLEET_VIEW,
    ("PUT", "/api/projects/{project}/repository-links/{repository}"): Permission.CONFIG_EDIT,
    ("POST", "/api/projects/{project}/repository-links/{repository}/retire"): Permission.CONFIG_EDIT,
    ("PUT", "/api/projects/{project}/landing-order"): Permission.CONFIG_EDIT,
    ("POST", "/api/projects/{project}/landing-order/position"): Permission.CONFIG_EDIT,
    ("POST", "/api/projects/{project}/scopes"): Permission.GRAPH_EDIT,
    ("GET", "/api/projects/{project}/scopes"): Permission.FLEET_VIEW,
    ("GET", "/api/projects/{project}/scopes/{slug}"): Permission.FLEET_VIEW,
    ("PATCH", "/api/projects/{project}/scopes/{slug}"): Permission.GRAPH_EDIT,
    ("POST", "/api/projects/{project}/scopes/{slug}/retire"): Permission.GRAPH_EDIT,
    ("POST", "/api/projects/{project}/scopes/{slug}/enable"): Permission.GRAPH_EDIT,
    ("GET", "/api/projects/{project}/scopes/{slug}/routines"): Permission.FLEET_VIEW,
    ("POST", "/api/projects/{project}/routines"): Permission.GRAPH_EDIT,
    ("GET", "/api/projects/{project}/routines"): Permission.FLEET_VIEW,
    ("GET", "/api/projects/{project}/findings"): Permission.FLEET_VIEW,
    ("GET", "/api/projects/{project}/findings/classes"): Permission.FLEET_VIEW,
    ("GET", "/api/projects/{project}/garden-proposals"): Permission.FLEET_VIEW,
    ("POST", "/api/projects/{project}/garden-proposals"): Permission.CHUNK_CONTROL,
    ("GET", "/api/projects/{project}/runs"): Permission.FLEET_VIEW,
    ("GET", "/api/projects/{project}/runs/{chunk_id}"): Permission.FLEET_VIEW,
    ("GET", "/api/config/schema/{kind}"): Permission.FLEET_VIEW,
    ("GET", "/api/config/changes"): Permission.FLEET_VIEW,
    ("GET", "/api/config/export"): Permission.FLEET_VIEW,
    ("POST", "/api/config/apply"): Permission.CONFIG_EDIT,
    ("GET", "/api/work-sources/{source}/items"): Permission.FLEET_VIEW,
    ("POST", "/api/work-sources/{source}/items"): Permission.CHUNK_CONTROL,
    ("GET", "/api/work-sources/{source}/items/{ref}"): Permission.FLEET_VIEW,
    ("PATCH", "/api/work-sources/{source}/items/{ref}"): Permission.CHUNK_CONTROL,
    ("DELETE", "/api/work-sources/{source}/items/{ref}"): Permission.CHUNK_CONTROL,
}


#: Fleet plane — every route mounted under ``/api/fleet/*`` but the identity route and the claim route carries
#: ``require_runner_principal``-at-router-level confinement; no per-route permission.
FLEET: set[tuple[str, str]] = {
    ("GET", "/api/fleet/queue/peek"),
    # The matched fleet peek — a second verb on the same
    # path; the ``GET`` above stays for a previous-minor caller.
    ("POST", "/api/fleet/queue/peek"),
    ("GET", "/api/fleet/chunk-statuses"),
    ("GET", "/api/fleet/chunks/{chunk_id}"),
    ("GET", "/api/fleet/chunks/{chunk_id}/work-items"),
    ("GET", "/api/fleet/chunks/{chunk_id}/pm-items"),  # the issue-#55 deprecated alias
    ("POST", "/api/fleet/chunks/{chunk_id}/pause"),
    ("POST", "/api/fleet/chunks/{chunk_id}/resume"),
    ("GET", "/api/fleet/summary"),
    ("GET", "/api/fleet/questions/{question_id}"),
    ("GET", "/api/fleet/chunks/{chunk_id}/envelope"),
    ("POST", "/api/fleet/chunks/{chunk_id}/artifact-check"),
    ("GET", "/api/fleet/chunks/{chunk_id}/garden/findings"),
    ("GET", "/api/fleet/chunks/{chunk_id}/garden/proposals"),
    ("GET", "/api/fleet/chunks/{chunk_id}/garden/proposals/page"),
    # A worker's own routine-run read of the counts/spend summaries — the
    # same operator-plane query criteria and response shaping, gated on the chunk's own
    # run context rather than operator credentials.
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/counts/files"),
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/counts/skills"),
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/counts/agent-types"),
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/counts/nodes"),
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/spend/nodes"),
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/spend/graphs"),
    ("GET", "/api/fleet/scopes"),
    ("GET", "/api/fleet/chunks/{chunk_id}/findings"),
    ("GET", "/api/fleet/chunks/{chunk_id}/findings/{finding_id}"),
    ("POST", "/api/fleet/chunks/{chunk_id}/hub-advance"),
    ("POST", "/api/fleet/chunks/{chunk_id}/route-token"),
    ("POST", "/api/fleet/chunks/{chunk_id}/release"),
    ("POST", "/api/fleet/chunks/{chunk_id}/completions"),
    ("POST", "/api/fleet/chunks/{chunk_id}/decisions"),
    ("POST", "/api/fleet/events"),
    ("POST", "/api/fleet/runners"),
    ("POST", "/api/fleet/runners/{runner_id}/heartbeats"),
    ("GET", "/api/fleet/runners/{runner_id}"),
    ("GET", "/api/fleet/runners/{runner_id}/decisions"),
    ("POST", "/api/fleet/transcripts"),  # the transcript lane's own push
    # A lease's own read-back of its shipped segments, confined by its own always-raising
    # ownership check rather than `assert_owns`.
    ("GET", "/api/fleet/chunks/{chunk_id}/transcript-segments"),
    # blizzard's own published `ArtifactScope.SYSTEM` set — read-only,
    # resolved live off the packaged set rather than any lease or chunk.
    ("GET", "/api/fleet/system-artifacts"),
    ("GET", "/api/fleet/system-artifacts/{name:path}"),
}


#: The claim route is gated by the principal without the retirement refusal: a retired runner's claim is
#: refused in-domain with the paused-denial body older runners parse.
FLEET_CLAIM: tuple[str, str] = ("POST", "/api/fleet/routes")


def api_routes(app: FastAPI) -> list[APIRoute]:
    """Every mounted :class:`APIRoute`, recursively unwrapped.

    This FastAPI version does not flatten an included router's routes onto
    ``app.routes`` eagerly, so recursing through ``an included router's ``original_router````
    is the stable way to enumerate the live surface regardless of nesting."""
    routes: list[APIRoute] = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            # The web root is the SPA shell, not an API surface — stays out of the
            # plane table.
            if route.path == "/":
                continue
            routes.append(route)
        elif isinstance(route, _MountedRouter):
            routes.extend(_api_routes_of(route))
    return routes


def _api_routes_of(included: _MountedRouter) -> list[APIRoute]:
    routes: list[APIRoute] = []
    for route in included.original_router.routes:
        if isinstance(route, APIRoute):
            routes.append(route)
        elif isinstance(route, _MountedRouter):
            routes.extend(_api_routes_of(route))
    return routes


def live_routes(app: FastAPI) -> set[tuple[str, str]]:
    """Every ``(method, path)`` pair the app actually mounts, ``HEAD`` excluded
    (FastAPI auto-adds it alongside every ``GET``, so it carries no separate
    classification)."""
    live: set[tuple[str, str]] = set()
    for route in api_routes(app):
        for method in route.methods or set():
            if method == "HEAD":
                continue
            live.add((method, route.path))
    return live


def dependency_names(route: APIRoute) -> set[str]:
    """The ``__name__`` of every dependency callable resolved for ``route`` (a callable instance's class
    name) — both
    route-level (``dependencies=[...]``) and router-level (attached at
    ``APIRouter(dependencies=[...])``, e.g. ``reject_runner_principal``/
    ``require_runner_principal``), so a single check covers both attachment shapes."""
    return {
        getattr(dep.call, "__name__", type(dep.call).__name__)
        for dep in route.dependant.dependencies
        if dep.call is not None
    }
