"""Source-reviewed claim, release, rekey and envelope refusal producers.

Expressions are the actual constructors, not the operations' problem declarations.
Framework validation and unhandled errors are recorded separately below.
"""

# Each branch's constructor is listed separately, even where branches share a status.
DIRECT_PRODUCERS = {
    ("POST", "/api/fleet/chunks/{chunk_id}/pause"): {
        "chunks.pause_chunk": {
            "ProblemHTTPException(partial(problems.chunk_not_found_problem, chunk_id))",
            "ProblemHTTPException(partial(problems.chunk_disappeared_problem, exc))",
            "ProblemHTTPException(partial(problems.chunk_not_pausable_problem, exc))",
        },
    },
    ("POST", "/api/fleet/chunks/{chunk_id}/resume"): {
        "chunks.resume_chunk": {
            "ProblemHTTPException(partial(problems.chunk_not_found_problem, chunk_id))",
        },
    },
    ("POST", "/api/fleet/chunks/{chunk_id}/hub-advance"): {
        "hub_advance": {
            "ProblemHTTPException(partial(problems.chunk_not_found_problem, chunk_id))",
            "ProblemHTTPException(problems.pinned_graph_missing_problem)",
        },
    },
    ("POST", "/api/fleet/chunks/{chunk_id}/completions"): {
        "submit_completion": {
            "ProblemHTTPException(partial(problems.chunk_not_found_problem, chunk_id))",
            "ProblemHTTPException(problems.pinned_graph_missing_problem)",
        },
    },
    ("POST", "/api/fleet/chunks/{chunk_id}/decisions"): {
        "submit_decision": {
            "ProblemHTTPException(partial(problems.chunk_not_found_problem, chunk_id))",
            "ProblemHTTPException(problems.pinned_graph_missing_problem)",
        },
    },
    ("POST", "/api/fleet/events"): {},
    ("POST", "/api/fleet/transcripts"): {},
    ("POST", "/api/fleet/runners"): {
        "register_runner": {
            "problem_response(problems.runner_not_found_problem(runner_id, instance=http_request.url.path))",
            "problem_response(problems.runner_version_problem(exc, instance=http_request.url.path))",
        },
    },
    ("POST", "/api/fleet/routes"): {
        "claim_route": {
            "problem_response(problems.chunk_not_found_problem(claim.chunk_id, instance=instance))",
            "problem_response(problems.pinned_graph_missing_problem(instance=instance))",
            "problem_response(problems.claim_runner_retired_problem(exc, chunk_id=claim.chunk_id, instance=instance))",
            "problem_response(problems.claim_paused_problem(exc, chunk_id=claim.chunk_id, instance=instance))",
            "problem_response(problems.claim_runner_unregistered_problem(exc, chunk_id=claim.chunk_id, instance=instance))",
            "problem_response(problems.claim_runner_version_problem(exc, chunk_id=claim.chunk_id, instance=instance))",
            "problem_response(problems.claim_terminal_problem(exc, instance=instance), status_code=409)",
            "problem_response(problems.claim_not_ready_problem(exc, instance=instance), status_code=409)",
            "problem_response(problems.claim_dependency_problem(exc, instance=instance), status_code=409)",
            "problem_response(denial, status_code=409)",
            "problem_response(problems.claim_conflict_problem(exc, chunk_id=claim.chunk_id, instance=instance), status_code=409)",
        },
    },
    ("POST", "/api/fleet/chunks/{chunk_id}/route-token"): {
        "rekey_route_token": {
            "problem_response(problems.rekey_not_holder_problem(exc, caller_id=fleet.caller_id(), instance=http_request.url.path))",
            "problem_response(problems.rekey_terminal_problem(exc, instance=http_request.url.path))",
        },
        # The pre-check's ownership exception is translated by the endpoint.
        "FleetRequest.assert_owns": {
            "ProblemHTTPException(partial(problems.runner_ownership_problem, caller_id=self.principal.runner_id, runner_id=runner_id))",
        },
    },
    ("POST", "/api/fleet/chunks/{chunk_id}/release"): {
        "release_chunk": {
            "problem_response(problems.chunk_not_found_problem(chunk_id, instance=http_request.url.path))",
            "problem_response(problems.release_not_holder_problem(exc, instance=http_request.url.path))",
            "problem_response(refusal)",
        },
    },
    ("POST", "/api/fleet/chunks/{chunk_id}/artifact-check"): {
        "check_artifact": {
            "ProblemHTTPException(partial(problems.chunk_not_found_problem, chunk_id))",
            "ProblemHTTPException(problems.pinned_graph_missing_problem)",
        },
    },
    ("GET", "/api/fleet/chunks/{chunk_id}/envelope"): {
        "get_envelope": {
            "ProblemHTTPException(partial(problems.chunk_not_found_problem, chunk_id))",
            "ProblemHTTPException(problems.pinned_graph_missing_problem)",
            "ProblemHTTPException(partial(problems.no_current_node_problem, exc))",
        },
    },
}

# Router dependencies (runner principal, tenant_bound -> resolve_tenant) run before each endpoint.
DEPENDENCY_PRODUCERS = {
    "RunnerAuth.demand": {
        "ProblemHTTPException(problems.authentication_revoked_problem)",
        "ProblemHTTPException(problem)",
    },
    "resolve_tenant": {
        "ProblemHTTPException(problems.tenant_credential_mismatch_problem)",
    },
    # _served calls _no_such_tenant if the credential's tenant is no longer open.
    "_no_such_tenant": {
        "ProblemHTTPException(problems.tenant_not_found_problem)",
    },
}

# App handlers reachable from these operations; refuse_closed_tenant lists only its runner branch.
EXCEPTION_HANDLERS = {
    "_refuse_retired_runner": {
        "JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={'detail': str(exc)})",
        "declared_problem_response(request, problems.runner_retired_problem(exc, instance=request.url.path))",
    },
    "refuse_closed_tenant": {
        "JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={'detail': UNRESOLVED_RUNNER_TOKEN})",
        "declared_problem_response(request, authentication_unresolved_problem(instance=request.url.path, detail=UNRESOLVED_RUNNER_TOKEN))",
    },
    "_log_unhandled_request_error": {
        "PlainTextResponse('Internal Server Error', status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)",
    },
}

# Framework 422 validation and the problem_http_exception rendering of the
# raises above are not separate constructors for these operations.
