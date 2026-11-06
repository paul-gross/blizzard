"""Source-reviewed fleet read producers, independent of response declarations.

Keys are hub operations; owners include delegated functions, not just route handlers.
Shared dependency and framework producers apply in addition to each entry.
"""

# The fleet router's runner-principal and tenant dependencies, plus the app handlers.
SHARED_PRODUCERS = {
    "hub_level.hub_services": {
        "ProblemHTTPException(problems.hub_store_unavailable_problem)",
    },
    "auth.RunnerAuth.demand": {
        "ProblemHTTPException(problems.authentication_revoked_problem)",
        "ProblemHTTPException(problem)",
    },
    "app._refuse_retired_runner": {
        "JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={'detail': str(exc)})",
        "declared_problem_response(request, problems.runner_retired_problem(exc, instance=request.url.path))",
    },
    "auth_session.resolve_tenant": {
        "ProblemHTTPException(problems.tenant_credential_mismatch_problem)",
    },
    "auth_session._no_such_tenant": {
        "ProblemHTTPException(problems.tenant_not_found_problem)",
    },
    # Tenant closure after resolution is mapped differently for a resolved runner.
    "auth_session.refuse_closed_tenant": {
        "JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={'detail': UNRESOLVED_RUNNER_TOKEN})",
        "problem_response(authentication_unresolved_problem(instance=request.url.path, detail=UNRESOLVED_RUNNER_TOKEN))",
    },
    # Unhandled exceptions, including failures in dependencies or rendering.
    "app._log_unhandled_request_error": {
        "PlainTextResponse('Internal Server Error', status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)",
    },
}

UNKNOWN_CHUNK = "ProblemHTTPException(partial(problems.chunk_not_found_problem, chunk_id))"
ROUTINE_RUN = {
    UNKNOWN_CHUNK,
    "ProblemHTTPException(partial(problems.no_run_context_problem, chunk_id))",
}
ANSWERED_FINDINGS = {
    UNKNOWN_CHUNK,
    "ProblemHTTPException(partial(problems.no_answered_proposal_problem, chunk_id))",
}

DIRECT_PRODUCERS = {
    ("GET", "/api/fleet/chunks/{chunk_id}/transcript-segments"): {
        "fleet.get_lease_transcript_segments": {
            "HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='lease segments in an inconsistent state')",
        },
        "fleet._demand_lease_owner": {
            "ProblemHTTPException(partial(problems.lease_ownership_problem, exc))",
        },
    },
    ("GET", "/api/fleet/system-artifacts"): {},
    ("GET", "/api/fleet/system-artifacts/{name:path}"): {
        "fleet.get_system_artifact_route": {
            "problem_response(problems.system_artifact_not_found_problem(name, instance=http_request.url.path))",
        },
        "system_artifacts.get_system_artifact": {
            "HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'no system artifact {name!r}')",
        },
    },
    ("GET", "/api/fleet/chunks/{chunk_id}"): {"chunks.get_chunk": {UNKNOWN_CHUNK}},
    ("GET", "/api/fleet/chunks/{chunk_id}/work-items"): {"chunks.get_work_items": {UNKNOWN_CHUNK}},
    ("GET", "/api/fleet/chunks/{chunk_id}/pm-items"): {"chunks.get_work_items": {UNKNOWN_CHUNK}},
    ("GET", "/api/fleet/chunk-statuses"): {},
    ("GET", "/api/fleet/queue/peek"): {},
    ("POST", "/api/fleet/queue/peek"): {},
    ("GET", "/api/fleet/summary"): {},
    ("GET", "/api/fleet/questions/{question_id}"): {
        "fleet.get_question": {
            "ProblemHTTPException(partial(problems.question_not_found_problem, question_id))",
        },
    },
    ("GET", "/api/fleet/chunks/{chunk_id}/garden/findings"): {"fleet._routine_run_or_404": ROUTINE_RUN},
    ("GET", "/api/fleet/chunks/{chunk_id}/garden/proposals"): {"fleet._routine_run_or_404": ROUTINE_RUN},
    ("GET", "/api/fleet/chunks/{chunk_id}/garden/proposals/page"): {
        "fleet._routine_run_or_404": ROUTINE_RUN,
        "fleet.get_garden_proposals_page": {
            "ProblemHTTPException(partial(problems.malformed_cursor_problem, exc))",
        },
    },
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/counts/files"): {"fleet._routine_run_or_404": ROUTINE_RUN},
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/counts/skills"): {"fleet._routine_run_or_404": ROUTINE_RUN},
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/counts/agent-types"): {"fleet._routine_run_or_404": ROUTINE_RUN},
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/counts/nodes"): {"fleet._routine_run_or_404": ROUTINE_RUN},
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/spend/nodes"): {"fleet._routine_run_or_404": ROUTINE_RUN},
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/spend/graphs"): {"fleet._routine_run_or_404": ROUTINE_RUN},
    ("GET", "/api/fleet/scopes"): {
        "fleet.get_scopes": {
            "problem_response(problems.chunk_not_found_problem(chunk_id, instance=http_request.url.path))"
        }
    },
    ("GET", "/api/fleet/chunks/{chunk_id}/findings"): {"fleet._answered_findings_or_404": ANSWERED_FINDINGS},
    ("GET", "/api/fleet/chunks/{chunk_id}/findings/{finding_id}"): {
        "fleet._answered_findings_or_404": ANSWERED_FINDINGS,
        "fleet.get_chunk_finding": {
            "ProblemHTTPException(partial(problems.finding_not_in_chunk_problem, chunk_id, finding_id))",
        },
    },
    ("GET", "/api/fleet/runners/{runner_id}/decisions"): {
        "fleet.FleetRequest.assert_owns": {
            "ProblemHTTPException(partial(problems.runner_ownership_problem, caller_id=self.principal.runner_id, runner_id=runner_id))",
        },
    },
    ("GET", "/api/fleet/runners/{runner_id}"): {
        "fleet.FleetRequest.assert_owns": {
            "ProblemHTTPException(partial(problems.runner_ownership_problem, caller_id=self.principal.runner_id, runner_id=runner_id))",
        },
        "fleet.get_runner": {
            "ProblemHTTPException(partial(problems.runner_not_found_problem, runner_id))",
            "ProblemHTTPException(partial(problems.runner_not_registered_problem, exc))",
        },
    },
}

# Framework 422 validation is not a route constructor; the envelope's direct
# producers live in tests/support/runner_refusal_inventory.py.
