"""Effective nested FastAPI dependencies, grouped by runner-reached operation.

Source: exported app's effective routes and their recursive dependant trees,
not refusal declarations. Names identify callables, not parameter bindings or
closure values; changes to those still require separate inspection.
"""

ANNOTATE = "blizzard.hub.app._annotate_chunk"
HUB = "blizzard.hub.api.hub_level.hub_services"
TENANT = {
    ANNOTATE,
    HUB,
    "blizzard.hub.api.auth_session.resolve_tenant",
    "blizzard.hub.api.auth_session.tenant_bound",
    "blizzard.hub.api.deps.tenant_services",
}
RUNNER = TENANT | {"blizzard.hub.api.auth.require_runner_principal"}
FLEET_REQUEST = RUNNER | {"blizzard.hub.api.fleet.FleetRequest.of"}
HOST = TENANT | {"blizzard.hub.api.auth.require_host_principal"}

# Keys are the hub's effective route paths, not client-side parameter names.
GROUPS = [
    (
        RUNNER,
        """
GET /api/fleet/chunk-statuses
GET /api/fleet/chunks/{chunk_id}
GET /api/fleet/chunks/{chunk_id}/envelope
GET /api/fleet/chunks/{chunk_id}/findings
GET /api/fleet/chunks/{chunk_id}/findings/{finding_id}
GET /api/fleet/chunks/{chunk_id}/garden/findings
GET /api/fleet/chunks/{chunk_id}/garden/proposals/page
GET /api/fleet/chunks/{chunk_id}/transcript-segments
GET /api/fleet/chunks/{chunk_id}/work-items
GET /api/fleet/questions/{question_id}
GET /api/fleet/scopes
GET /api/fleet/summary
GET /api/fleet/system-artifacts
GET /api/fleet/system-artifacts/{name}
POST /api/fleet/chunks/{chunk_id}/artifact-check
POST /api/fleet/chunks/{chunk_id}/hub-advance
POST /api/fleet/chunks/{chunk_id}/pause
POST /api/fleet/chunks/{chunk_id}/resume
POST /api/fleet/queue/peek
""",
    ),
    (
        RUNNER | {"blizzard.hub.api.fleet.AnalyticsWindow.of"},
        """
GET /api/fleet/chunks/{chunk_id}/analytics/counts/agent-types
GET /api/fleet/chunks/{chunk_id}/analytics/counts/files
GET /api/fleet/chunks/{chunk_id}/analytics/counts/nodes
GET /api/fleet/chunks/{chunk_id}/analytics/counts/skills
GET /api/fleet/chunks/{chunk_id}/analytics/spend/graphs
GET /api/fleet/chunks/{chunk_id}/analytics/spend/nodes
""",
    ),
    (
        FLEET_REQUEST,
        """
GET /api/fleet/runners/{runner_id}
GET /api/fleet/runners/{runner_id}/decisions
POST /api/fleet/chunks/{chunk_id}/completions
POST /api/fleet/chunks/{chunk_id}/decisions
POST /api/fleet/chunks/{chunk_id}/release
POST /api/fleet/chunks/{chunk_id}/route-token
POST /api/fleet/events
POST /api/fleet/runners
POST /api/fleet/transcripts
""",
    ),
    (
        {ANNOTATE},
        """
GET /api/auth/authorize
GET /api/auth/jwks.json
GET /api/me
""",
    ),
    ({ANNOTATE, HUB}, "GET /api/fleet/identity"),
    (
        HOST,
        """
GET /api/fleet/host
POST /api/fleet/host
POST /api/fleet/host/events
POST /api/fleet/host/runners
POST /api/fleet/host/runners/{runner_id}/retire
""",
    ),
    (
        TENANT
        | {
            "blizzard.hub.api.auth.require_runner_principal_even_if_retired",
            "blizzard.hub.api.fleet.FleetRequest.of_even_if_retired",
        },
        "POST /api/fleet/routes",
    ),
    (
        TENANT
        | {
            "blizzard.hub.api.auth.reject_runner_principal",
            "blizzard.hub.api.auth_session.require.<locals>._dependency",
        },
        "POST /api/hosts",
    ),
]


def inventory() -> dict[tuple[str, str], set[str]]:
    result: dict[tuple[str, str], set[str]] = {}
    for dependencies, lines in GROUPS:
        for line in lines.strip().splitlines():
            method, path = line.split()
            key = (method, path)
            assert key not in result, f"duplicate dependency inventory {key}"
            result[key] = set(dependencies)
    return result
