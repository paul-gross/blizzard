"""Reviewed runner-reached non-fleet candidates, including successful responses."""

from tests.support.me_producer_inventory import REVIEWED as ME

_PROBLEM = (
    "blizzard.hub.api.problem_rendering.problem_response",
    "JSONResponse(content=content, status_code=_http_status(content, status_code), headers=headers, media_type=PROBLEM_MEDIA_TYPE)",
)

REVIEWED = {
    **ME,
    ("GET", "/api/auth/authorize"): {
        _PROBLEM,
        (
            "blizzard.hub.api.idp._unknown_client",
            "problem_response(problems.federation_client_refused_problem(instance=request.url.path))",
        ),
        (
            "blizzard.hub.api.idp.authorize",
            "problem_response(problems.idp_disabled_problem(instance=request.url.path))",
        ),
        (
            "blizzard.hub.api.idp.authorize",
            "problem_response(problems.federation_response_mode_problem(response_mode, instance=request.url.path))",
        ),
        (
            "blizzard.hub.api.idp.authorize",
            "problem_response(problems.cli_redirect_refused_problem(instance=request.url.path))",
        ),
        (
            "blizzard.hub.api.idp.authorize",
            "problem_response(problems.cli_pkce_required_problem(instance=request.url.path))",
        ),
        (
            "blizzard.hub.api.idp.authorize",
            "problem_response(problems.federation_no_provider_problem(instance=request.url.path))",
        ),
        (
            "blizzard.hub.api.idp.authorize",
            "RedirectResponse(f\"/api/auth/{providers[0].name}/authorize?return_to={quote(return_to, safe='')}\")",
        ),
        ("blizzard.hub.api.idp.authorize", "RedirectResponse(f\"/login?return_to={quote(return_to, safe='')}\")"),
    },
    ("GET", "/api/auth/jwks.json"): {
        _PROBLEM,
        (
            "blizzard.hub.api.idp.jwks",
            "problem_response(problems.idp_disabled_problem(instance=request.url.path))",
        ),
    },
    ("POST", "/api/hosts"): {
        _PROBLEM,
        (
            "blizzard.hub.api.auth.RunnerAuth.refuse_runner",
            "ProblemHTTPException(partial(problems.runner_credential_forbidden_problem, principal.runner_id))",
        ),
        (
            "blizzard.hub.api.auth_session._no_such_tenant",
            "ProblemHTTPException(problems.tenant_not_found_problem)",
        ),
        (
            "blizzard.hub.api.auth_session._tenant_required",
            "ProblemHTTPException(problems.tenant_required_problem)",
        ),
        (
            "blizzard.hub.api.auth_session.granted",
            "ProblemHTTPException(problems.machine_credential_forbidden_problem)",
        ),
        (
            "blizzard.hub.api.auth_session.granted",
            "ProblemHTTPException(partial(problems.permission_required_problem, permission))",
        ),
        (
            "blizzard.hub.api.auth_session.resolve_tenant",
            "ProblemHTTPException(problems.tenant_credential_mismatch_problem)",
        ),
        (
            "blizzard.hub.api.auth_session.resolve_tenant",
            "ProblemHTTPException(partial(problems.authentication_required_problem, detail='authentication required'))",
        ),
        (
            "blizzard.hub.api.hub_level.hub_services",
            "ProblemHTTPException(problems.hub_store_unavailable_problem)",
        ),
        (
            "blizzard.hub.api.hosts.add_host",
            "problem_response(host_name_taken_problem(exc, instance=http_request.url.path))",
        ),
        (
            "blizzard.hub.api.hosts.add_host",
            "problem_response(runner_not_found_problem(exc.runner_id, instance=http_request.url.path))",
        ),
        (
            "blizzard.hub.api.hosts.add_host",
            "problem_response(runner_already_hosted_problem(exc, instance=http_request.url.path))",
        ),
        (
            "blizzard.hub.api.hosts.add_host",
            "problem_response(runner_retired_problem(exc, instance=http_request.url.path, status_code=409))",
        ),
        (
            "blizzard.hub.api.hosts.add_host",
            "problem_response(runner_lifecycle_conflict_problem(exc, instance=http_request.url.path))",
        ),
        (
            "blizzard.hub.api.hosts.add_host",
            "HostAddResponse(host_id=added.host_id, host_name=added.name, token=added.token, adopted=list(added.adopted))",
        ),
    },
}
