"""Source-reviewed refusal producers for the runner operations the guards pin directly.

Entries name the actual producing function and constructor, not an OpenAPI
declaration. Dependencies and app-level handlers remain separate evidence and
must be inventoried before this can establish absence of other producers.
"""

# GET /api/me refuses an anonymous request; an unresolvable tenant header is not a 404 here.
DIRECT_PRODUCERS = {
    ("GET", "/api/me"): {
        "me": {
            "problem_response(authentication_required_problem(instance=request.url.path, detail='not authenticated'))"
        },
    },
    # Missing chunk, missing pinned graph, or a chunk with no current node.
    ("GET", "/api/fleet/chunks/{chunk_id}/envelope"): {
        "get_envelope": {
            "ProblemHTTPException(partial(problems.chunk_not_found_problem, chunk_id))",
            "ProblemHTTPException(problems.pinned_graph_missing_problem)",
            "ProblemHTTPException(partial(problems.no_current_node_problem, exc))",
        },
    },
}
