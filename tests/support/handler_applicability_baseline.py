"""Reviewed registrations conservatively considered for each runner operation."""

HANDLERS = set(
    """\
blizzard.foundation.flags.flags.FlagOffError -> blizzard.hub.api.flag_guard.refuse_flag_off d80393fbe43f072cef6030d6d2233a80d7566adce9b2b9b63ac6a2df88f125ce
blizzard.foundation.flags.flags.FlagOffError -> blizzard.hub.api.flag_guard.refuse_flag_off transitive bd6b5e35ebc286239a9e826392f1d2e56e00a582713ec985f63a2f6563e1a919
blizzard.hub.auth.memberships.MembershipRoleRefused -> blizzard.hub.api.members.refuse_membership_role c5ec0de2e41c981c628394954d27178b02f1ec0e121a94f4a2edc14bef0da5b6
blizzard.hub.domain.config.work_sources.BuiltInWorkSource -> blizzard.hub.api.work_source_params.refuse_built_in_work_source 06d3d22277234cf8e5d83a2f040b667a254c6684a40060d2db097517a46dc8ba
blizzard.hub.domain.projects.model.ProjectRequired -> blizzard.hub.api.project_params.refuse_project_required d5b91458019551c3d9d1218887a6d415ca44e15d52578f4273e6c6135b9acaa5
blizzard.hub.domain.projects.model.ProjectRequired -> blizzard.hub.api.project_params.refuse_project_required transitive 9939d90903a72eaf13323119725187bdee586daabb7dcb1249c4affac8fb3532
blizzard.hub.domain.runners.host.HostRetired -> blizzard.hub.app._refuse_retired_host 730639be1f8c671f92139521f68c1279ba86182b05a29fd2ca699ccee0cb6238
blizzard.hub.domain.runners.host.HostRetired -> blizzard.hub.app._refuse_retired_host transitive 7f20f673ff40dfbe356041058e18837a928e7d45c1d1799e88d93f17bdd0886f
blizzard.hub.domain.runners.registration.RunnerRetired -> blizzard.hub.app._refuse_retired_runner e477618eac8c66823e63731fb522db3d7b126a1069868157cdb57571a3639ee5
blizzard.hub.domain.runners.registration.RunnerRetired -> blizzard.hub.app._refuse_retired_runner transitive 58afe328242a9834ad667986dd3a4e0e36b577100723621a41ebec5ef3c7c8ae
blizzard.hub.tenancy.TenantClosed -> blizzard.hub.api.auth_session.refuse_closed_tenant 262fc047ba44d05d1b0169ff83843c4fea4de761c1d59ac14e336c92ddbf82e8
blizzard.hub.tenancy.TenantClosed -> blizzard.hub.api.auth_session.refuse_closed_tenant transitive 7bef38e51b2b4b54364f3179d3f3697443e58de442e630bce641dccba53c300b
blizzard.hub.tenancy.UnknownTenant -> blizzard.hub.api.auth_session.refuse_closed_tenant 262fc047ba44d05d1b0169ff83843c4fea4de761c1d59ac14e336c92ddbf82e8
blizzard.hub.tenancy.UnknownTenant -> blizzard.hub.api.auth_session.refuse_closed_tenant transitive 7bef38e51b2b4b54364f3179d3f3697443e58de442e630bce641dccba53c300b
builtins.Exception -> blizzard.hub.app._log_unhandled_request_error 3c9fb426644d5b065e019bc44b5a62ef1f0d4187613215789f0cada4610d8345
builtins.Exception -> blizzard.hub.app._log_unhandled_request_error transitive 048a87f614b3110f4c354790a858a463b2b5bc21988211b6abcfc7ddf6503b1a
fastapi.exceptions.RequestValidationError -> blizzard.hub.app._validation_error a2332f8ec524dd24029ed2dd42468c94c6d9cf041b690538b951810755fe0313
fastapi.exceptions.RequestValidationError -> blizzard.hub.app._validation_error transitive 51c7cb53b992d9c060204a789eaef334f4bfbd11c5f7cdf27029beb97c560a8e
fastapi.exceptions.WebSocketRequestValidationError -> fastapi.exception_handlers.websocket_request_validation_exception_handler 32773212f2be401c23e7d840347d435f9498bb340584921433d4b18aa2b229d6
starlette.exceptions.HTTPException -> blizzard.hub.api.problem_exceptions.problem_http_exception b7a7604130ccac17c4e52f4a950fead605e0d4b271b0e4dc2ddd0d4f5ce34075
starlette.exceptions.HTTPException -> blizzard.hub.api.problem_exceptions.problem_http_exception transitive 3c3ebd4a6fe8493ff7ac7a37cc00cda275b79dcc0610923a1654aa99aec8a0aa
""".splitlines()
)

OPERATIONS: set[tuple[str, str]] = {
    (method, path)
    for line in """\
GET /api/auth/authorize
GET /api/auth/jwks.json
GET /api/fleet/chunk-statuses
GET /api/fleet/chunks/{chunk_id}
GET /api/fleet/chunks/{chunk_id}/analytics/counts/agent-types
GET /api/fleet/chunks/{chunk_id}/analytics/counts/files
GET /api/fleet/chunks/{chunk_id}/analytics/counts/nodes
GET /api/fleet/chunks/{chunk_id}/analytics/counts/skills
GET /api/fleet/chunks/{chunk_id}/analytics/spend/graphs
GET /api/fleet/chunks/{chunk_id}/analytics/spend/nodes
GET /api/fleet/chunks/{chunk_id}/envelope
GET /api/fleet/chunks/{chunk_id}/findings
GET /api/fleet/chunks/{chunk_id}/findings/{finding_id}
GET /api/fleet/chunks/{chunk_id}/garden/findings
GET /api/fleet/chunks/{chunk_id}/garden/proposals/page
GET /api/fleet/chunks/{chunk_id}/transcript-segments
GET /api/fleet/chunks/{chunk_id}/work-items
GET /api/fleet/host
GET /api/fleet/identity
GET /api/fleet/questions/{question_id}
GET /api/fleet/runners/{runner_id}
GET /api/fleet/runners/{runner_id}/decisions
GET /api/fleet/scopes
GET /api/fleet/summary
GET /api/fleet/system-artifacts
GET /api/fleet/system-artifacts/{name}
GET /api/me
POST /api/fleet/chunks/{chunk_id}/completions
POST /api/fleet/chunks/{chunk_id}/decisions
POST /api/fleet/chunks/{chunk_id}/hub-advance
POST /api/fleet/chunks/{chunk_id}/pause
POST /api/fleet/chunks/{chunk_id}/release
POST /api/fleet/chunks/{chunk_id}/resume
POST /api/fleet/chunks/{chunk_id}/route-token
POST /api/fleet/events
POST /api/fleet/host
POST /api/fleet/host/events
POST /api/fleet/host/runners
POST /api/fleet/host/runners/{runner_id}/retire
POST /api/fleet/queue/peek
POST /api/fleet/routes
POST /api/fleet/runners
POST /api/fleet/transcripts
POST /api/hosts
POST /api/fleet/chunks/{chunk_id}/artifact-check
""".splitlines()
    for method, _, path in [line.partition(" ")]
}

HTTP_HANDLER = "blizzard.hub.api.problem_exceptions.problem_http_exception"
PROBLEM_EXCEPTION = "blizzard.hub.api.problem_exceptions.ProblemHTTPException"
HUB_SERVICES = f"blizzard.hub.api.hub_level.hub_services raises {PROBLEM_EXCEPTION}"
TENANT = f"blizzard.hub.api.auth_session.resolve_tenant raises {PROBLEM_EXCEPTION}"
DIRECT_ENDPOINTS = {
    ("POST", "/api/fleet/chunks/{chunk_id}/artifact-check"): ("check_artifact", PROBLEM_EXCEPTION),
    ("POST", "/api/fleet/chunks/{chunk_id}/hub-advance"): ("hub_advance", PROBLEM_EXCEPTION),
    ("GET", "/api/fleet/chunks/{chunk_id}/findings/{finding_id}"): ("get_chunk_finding", PROBLEM_EXCEPTION),
    ("POST", "/api/fleet/chunks/{chunk_id}/decisions"): ("submit_decision", PROBLEM_EXCEPTION),
    ("GET", "/api/fleet/runners/{runner_id}"): ("get_runner", PROBLEM_EXCEPTION),
    ("GET", "/api/fleet/chunks/{chunk_id}/transcript-segments"): (
        "get_lease_transcript_segments",
        "fastapi.exceptions.HTTPException",
    ),
    ("GET", "/api/fleet/chunks/{chunk_id}/garden/proposals/page"): ("get_garden_proposals_page", PROBLEM_EXCEPTION),
    ("GET", "/api/fleet/questions/{question_id}"): ("get_question", PROBLEM_EXCEPTION),
    ("GET", "/api/fleet/chunks/{chunk_id}/envelope"): ("get_envelope", PROBLEM_EXCEPTION),
    ("POST", "/api/fleet/chunks/{chunk_id}/completions"): ("submit_completion", PROBLEM_EXCEPTION),
}
DIRECT_DISPATCH: dict[tuple[str, str], dict[str, set[str]]] = {
    operation: {
        HTTP_HANDLER: {HUB_SERVICES}
        | ({TENANT} if operation != ("GET", "/api/fleet/identity") else set())
        | (
            {f"blizzard.hub.api.fleet.{DIRECT_ENDPOINTS[operation][0]} raises {DIRECT_ENDPOINTS[operation][1]}"}
            if operation in DIRECT_ENDPOINTS
            else set()
        )
    }
    for operation in OPERATIONS
    if operation[1].startswith("/api/fleet/") or operation == ("POST", "/api/hosts")
}
