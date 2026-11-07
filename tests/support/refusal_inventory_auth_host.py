"""Source-reviewed runner-reached hub auth and host refusal producers.

The keys and constructor strings follow tests/support/runner_refusal_inventory.py. Shared
dependencies and applicable app handlers are included per operation; framework
validation and unhandled failures are recorded separately, not coverage proof.
"""

_HUB = {
    "hub_level.hub_services": {
        "ProblemHTTPException(problems.hub_store_unavailable_problem)",
    },
}

_HOST_AUTH = {
    **_HUB,
    "auth.HostAuth.demand": {
        "ProblemHTTPException(problems.authentication_revoked_problem)",
        "ProblemHTTPException(problem)",
    },
    "app._refuse_retired_host": {
        'JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": str(exc)})',
        "declared_problem_response(request, problems.host_retired_problem(exc, instance=request.url.path))",
    },
}

_TENANT = {
    "auth_session.resolve_tenant": {
        "ProblemHTTPException(problems.tenant_credential_mismatch_problem)",
        "ProblemHTTPException(partial(problems.authentication_required_problem, detail='authentication required'))",
    },
    "auth_session._no_such_tenant": {
        "ProblemHTTPException(problems.tenant_not_found_problem)",
    },
    "auth_session._tenant_required": {
        "ProblemHTTPException(problems.tenant_required_problem)",
    },
    "auth_session.refuse_closed_tenant": {
        'JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": UNRESOLVED_RUNNER_TOKEN})',
        'JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": _NO_SUCH_TENANT})',
        "problem_response(authentication_unresolved_problem(instance=request.url.path, detail=UNRESOLVED_RUNNER_TOKEN))",
        "problem_response(tenant_not_found_problem(instance=request.url.path))",
    },
}

_HELD_HOST = {
    "fleet_host._unresolved": {
        "problem_response(problems.authentication_unresolved_problem(instance=request.url.path, detail='bearer token does not resolve'))",
    },
}

DIRECT_PRODUCERS = {
    ("GET", "/api/auth/jwks.json"): {
        **_HUB,
        "idp.jwks": {"problem_response(problems.idp_disabled_problem(instance=request.url.path))"},
        "idp.IdpSurface._absent": {
            'HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="the IdP surface is not enabled")',
        },
    },
    ("GET", "/api/auth/authorize"): {
        **_HUB,
        "idp.IdpSurface._absent": {
            'HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="the IdP surface is not enabled")',
        },
        "idp.authorize": {
            "problem_response(problems.idp_disabled_problem(instance=request.url.path))",
            "problem_response(problems.federation_response_mode_problem(response_mode, instance=request.url.path))",
            "problem_response(problems.cli_redirect_refused_problem(instance=request.url.path))",
            "problem_response(problems.cli_pkce_required_problem(instance=request.url.path))",
            "problem_response(problems.federation_no_provider_problem(instance=request.url.path))",
        },
        "idp._unknown_client": {
            "problem_response(problems.federation_client_refused_problem(instance=request.url.path))",
        },
        "app._refuse_retired_runner": {
            'JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": str(exc)})',
            "declared_problem_response(request, problems.runner_retired_problem(exc, instance=request.url.path))",
        },
        "app._refuse_retired_host": {
            'JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": str(exc)})',
            "declared_problem_response(request, problems.host_retired_problem(exc, instance=request.url.path))",
        },
    },
    ("GET", "/api/fleet/identity"): {
        **_HUB,
        "fleet_identity.get_runner_identity": {
            "problem_response(runner_identity_problem(exc, instance=request.url.path))",
        },
    },
    ("GET", "/api/me"): {
        **_HUB,
        "me.me": {
            "problem_response(authentication_required_problem(instance=request.url.path, detail='not authenticated'))"
        },
    },
    ("POST", "/api/hosts"): {
        **_HUB,
        **_TENANT,
        "auth.RunnerAuth.refuse_runner": {
            "ProblemHTTPException(partial(problems.runner_credential_forbidden_problem, principal.runner_id))",
        },
        "auth_session.granted": {
            "ProblemHTTPException(problems.machine_credential_forbidden_problem)",
            "ProblemHTTPException(partial(problems.permission_required_problem, permission))",
        },
        "hosts.add_host": {
            "problem_response(host_name_taken_problem(exc, instance=http_request.url.path))",
            "problem_response(runner_not_found_problem(exc.runner_id, instance=http_request.url.path))",
            "problem_response(runner_already_hosted_problem(exc, instance=http_request.url.path))",
            "problem_response(runner_retired_problem(exc, instance=http_request.url.path, status_code=409))",
            "problem_response(runner_lifecycle_conflict_problem(exc, instance=http_request.url.path))",
        },
    },
    ("GET", "/api/fleet/host"): {**_HOST_AUTH, **_TENANT, **_HELD_HOST},
    ("POST", "/api/fleet/host"): {
        **_HOST_AUTH,
        **_TENANT,
        **_HELD_HOST,
        "fleet_host.report_host": {
            "problem_response(problems.host_retired_problem(exc, instance=http_request.url.path))"
        },
    },
    ("POST", "/api/fleet/host/events"): {
        **_HOST_AUTH,
        **_TENANT,
        **_HELD_HOST,
        "fleet_host.ingest_host_facts": {
            "problem_response(problems.host_retired_problem(exc, instance=http_request.url.path))"
        },
    },
    ("POST", "/api/fleet/host/runners"): {
        **_HOST_AUTH,
        **_TENANT,
        **_HELD_HOST,
        "fleet_host.add_host_runner": {
            "problem_response(problems.unknown_served_project_problem(exc, instance=http_request.url.path))",
            "problem_response(problems.host_retired_problem(exc, instance=http_request.url.path))",
        },
    },
    ("POST", "/api/fleet/host/runners/{runner_id}/retire"): {
        **_HOST_AUTH,
        **_TENANT,
        "fleet_host.retire_host_runner": {
            "problem_response(problems.runner_not_found_problem(runner_id, instance=http_request.url.path))",
            "problem_response(problems.runner_retire_interrupted_problem(exc, instance=http_request.url.path))",
            "problem_response(problems.runner_holds_routes_problem(exc, instance=http_request.url.path))",
            "problem_response(problems.runner_lifecycle_conflict_problem(exc, instance=http_request.url.path))",
        },
    },
}

# Framework 422 validation and the generic unhandled-error 500 are not
# operation-specific constructors, so neither is listed above.
