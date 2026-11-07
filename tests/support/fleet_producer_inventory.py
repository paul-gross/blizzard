"""Reviewed runner-reached fleet candidates, including non-refusal models."""

import ast

from tests.support.refusal_inventory_auth_host import DIRECT_PRODUCERS as HOST
from tests.support.refusal_inventory_claims import DIRECT_PRODUCERS as CLAIM
from tests.support.refusal_inventory_delegated import DIRECT_PRODUCERS as READS
from tests.support.stable_ast import unparse

CLAIM_OPERATION = ("POST", "/api/fleet/routes")
HOST_OPERATIONS = {
    ("GET", "/api/fleet/host"),
    ("POST", "/api/fleet/host"),
    ("POST", "/api/fleet/host/events"),
    ("POST", "/api/fleet/host/runners"),
    ("POST", "/api/fleet/host/runners/{runner_id}/retire"),
}


def _entries(producers: dict[str, set[str]], default_module: str = "") -> set[tuple[str, str]]:
    return {
        (
            f"blizzard.hub.api.{default_module + owner if '.' not in owner or owner.startswith('FleetRequest.') else owner}",
            unparse(ast.parse(expr, mode="eval").body),
        )
        for owner, expressions in producers.items()
        for expr in expressions
    }


_SHARED = _entries(
    {
        owner: expressions
        for owner, expressions in HOST[("GET", "/api/fleet/host")].items()
        if owner
        in {
            "hub_level.hub_services",
            "auth_session.resolve_tenant",
            "auth_session._no_such_tenant",
            "auth_session._tenant_required",
        }
    }
) | {
    (
        "blizzard.hub.api.problem_rendering.problem_response",
        "JSONResponse(content=content, status_code=_http_status(content, status_code), headers=headers, media_type=PROBLEM_MEDIA_TYPE)",
    )
}

_RECORD = (
    "blizzard.hub.api.fleet_host._record",
    "HostRecordResponse(host_id=host.host_id, tenant_id=principal.tenant_id, host_name=host.name, registered_at=None if host.registered_at is None else iso_utc(host.registered_at), last_seen_at=None if host.last_seen_at is None else iso_utc(host.last_seen_at), runners=[HostRunnerView(runner_id=r.runner_id, runner_name=r.name) for r in runners])",
)

REVIEWED = {
    CLAIM_OPERATION: _SHARED
    | _entries(CLAIM[CLAIM_OPERATION], "fleet.")
    | {
        (
            "blizzard.hub.api.fleet.claim_route",
            "RouteClaimResponse(chunk_id=result.route.chunk_id, runner_id=result.route.runner_id, workspace_id=result.route.workspace_id, environment_ids=result.route.environment_ids, envelope=node_steps.node_envelope(result.envelope), route_token=result.route_token)",
        ),
    },
    **{op: _SHARED | _entries(HOST[op]) for op in HOST_OPERATIONS},
}
REVIEWED[("GET", "/api/fleet/host")].add(_RECORD)
REVIEWED[("POST", "/api/fleet/host")].add(_RECORD)
REVIEWED[("POST", "/api/fleet/host/runners")].add(
    (
        "blizzard.hub.api.fleet_host.add_host_runner",
        "RunnerAddResponse(runner_id=added.runner_id, runner_name=added.name, token=added.token)",
    )
)
REVIEWED[("POST", "/api/fleet/host/runners/{runner_id}/retire")].add(
    (
        "blizzard.hub.api.fleet_host.retire_host_runner",
        "RunnerRetireResponse(runner=registry_view(services.fleet.get_liveness(refreshed), now=services.clock.now(), projects=served_projects(services, refreshed.served), hosts=hosts_of(services, [refreshed]), executors=executors_for(services, [refreshed])), released_chunk_ids=[r.chunk_id for r in outcome.released])",
    )
)

# The effective routes normalize the authored inventory's :path converter.
READ_OPERATIONS = {
    ("GET", "/api/fleet/chunk-statuses"),
    ("GET", "/api/fleet/chunks/{chunk_id}"),
    *(
        ("GET", f"/api/fleet/chunks/{{chunk_id}}/analytics/{suffix}")
        for suffix in (
            "counts/agent-types",
            "counts/files",
            "counts/nodes",
            "counts/skills",
            "spend/graphs",
            "spend/nodes",
        )
    ),
    *(
        ("GET", f"/api/fleet/chunks/{{chunk_id}}/{suffix}")
        for suffix in (
            "findings",
            "findings/{finding_id}",
            "garden/findings",
            "garden/proposals/page",
            "transcript-segments",
            "work-items",
        )
    ),
    ("GET", "/api/fleet/questions/{question_id}"),
    ("GET", "/api/fleet/runners/{runner_id}"),
    ("GET", "/api/fleet/runners/{runner_id}/decisions"),
    ("GET", "/api/fleet/scopes"),
    ("GET", "/api/fleet/summary"),
    ("GET", "/api/fleet/system-artifacts"),
    ("GET", "/api/fleet/system-artifacts/{name}"),
    ("POST", "/api/fleet/queue/peek"),
}
WRITE_OPERATIONS = set(CLAIM) - {CLAIM_OPERATION}
for op in READ_OPERATIONS:
    source_op = (op[0], op[1].replace("{name}", "{name:path}"))
    REVIEWED[op] = _SHARED | _entries(READS[source_op])
for op in WRITE_OPERATIONS:
    REVIEWED[op] = _SHARED | _entries(CLAIM[op], "fleet.")
REVIEWED[("GET", "/api/fleet/identity")] = _SHARED | _entries(HOST[("GET", "/api/fleet/identity")])

# The operator permission dependency is also mounted on these effective routes.
_GRANTED = _entries({"auth_session.granted": HOST[("POST", "/api/hosts")]["auth_session.granted"]})
for op in (
    ("GET", "/api/fleet/chunks/{chunk_id}"),
    ("GET", "/api/fleet/chunks/{chunk_id}/work-items"),
    ("POST", "/api/fleet/chunks/{chunk_id}/pause"),
    ("POST", "/api/fleet/chunks/{chunk_id}/resume"),
):
    REVIEWED[op] |= _GRANTED

# Successful response models the conservative census also names as candidates.
_COUNTS = (
    "blizzard.hub.api.analytics.counts_response",
    "AnalyticsCountsResponse(counts=[AnalyticsCountView(key=row.key, count=row.count, graph_name=row.graph_name, node_name=row.node_name) for row in rows])",
)
_SPEND = (
    "blizzard.hub.api.analytics.spend_response",
    "AnalyticsSpendResponse(spend=[AnalyticsSpendView(key=s.key, input_tokens=s.total.input_tokens, output_tokens=s.total.output_tokens, cache_read_tokens=s.total.cache_read_tokens, cache_create_tokens=s.total.cache_create_tokens, cost_usd=s.total.cost_usd, cost_partial=s.total.cost_partial, estimated_cost_usd=s.total.estimated_cost_usd, graph_name=s.graph_name, node_name=s.node_name) for s in stats])",
)
for op in READ_OPERATIONS:
    if "/analytics/counts/" in op[1]:
        REVIEWED[op].add(_COUNTS)
    elif "/analytics/spend/" in op[1]:
        REVIEWED[op].add(_SPEND)
_APPLY = (
    "blizzard.hub.api.node_steps.apply_response",
    "ApplyResponse(outcome=outcome, next_envelope=node_envelope(envelope) if envelope is not None else None, detail=detail)",
)
for suffix in ("completions", "decisions"):
    REVIEWED[("POST", f"/api/fleet/chunks/{{chunk_id}}/{suffix}")].add(_APPLY)
_MODELS = {
    ("POST", "/api/fleet/chunks/{chunk_id}/artifact-check"): {
        (
            "blizzard.hub.api.fleet.check_artifact",
            "ArtifactCheckResponse(refusal=services.garden_stage_check.refusal(graph, body.name, body.content))",
        )
    },
    ("GET", "/api/fleet/runners/{runner_id}/decisions"): {
        (
            "blizzard.hub.api.fleet.list_runner_decisions",
            "OpenDecisionsResponse(decisions=[to_decision_view(d, runner_names, labels.fields(d.project_id)) for d in rows])",
        )
    },
    ("POST", "/api/fleet/chunks/{chunk_id}/hub-advance"): {
        (
            "blizzard.hub.api.fleet.hub_advance",
            "HubAdvanceResponse(chunk_id=chunk_id, status=facts.status(), ran=False, detail='not parked at a hub command node')",
        ),
        (
            "blizzard.hub.api.fleet.hub_advance",
            "HubAdvanceResponse(chunk_id=chunk_id, status=facts.status(), ran=False, detail=describe_standing(facts, node, services.flows.visit_standing(chunk_id, node_id=node.node_id, epoch=epoch), epoch=epoch, now=services.clock.now(), flow_name=_flow_namer(services)))",
        ),
    },
    ("POST", "/api/fleet/chunks/{chunk_id}/release"): {
        ("blizzard.hub.api.fleet.release_chunk", "ReleaseResponse(chunk_id=chunk_id, released_id=released_id)")
    },
    ("POST", "/api/fleet/chunks/{chunk_id}/route-token"): {
        (
            "blizzard.hub.api.fleet.rekey_route_token",
            "RouteTokenRekeyResponse(chunk_id=chunk_id, route_token=route_token)",
        )
    },
    ("POST", "/api/fleet/runners"): {
        (
            "blizzard.hub.api.fleet.register_runner",
            "RunnerRegistrationResponse(runner_id=runner_id, runner_name=registered.name, first_registration=registered.first, tenant_id=fleet.scope.tenant, tenant_name=fleet.tenant_name, projects=runners_api.served_views(registered.projects, runners_api.served_projects(services, registered.projects)), bindings=runners_api.binding_views(registered.bindings, flags, request.agents), subscription=registered.subscription, subscription_unrostered=flags.subscription_unrostered, hub_version=registered.version.hub_version, window=VersionWindowView(floor=registered.version.floor, ceiling=registered.version.ceiling), unenforceable_gates=runners_api.unenforceable_views(request.gates, services.node_executors.executors_by_node_name() if request.gates else {}))",
        )
    },
}
for op, models in _MODELS.items():
    REVIEWED[op] |= models

_RUNNER_AUTH = {
    ("blizzard.hub.api.auth.RunnerAuth.demand", expression)
    for expression in (
        "ProblemHTTPException(problems.authentication_revoked_problem)",
        "ProblemHTTPException(problem)",
    )
}
for op in READ_OPERATIONS | WRITE_OPERATIONS | {CLAIM_OPERATION}:
    REVIEWED[op] |= _RUNNER_AUTH
