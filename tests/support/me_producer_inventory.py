"""Source-reviewed producer candidates for the real GET /api/me operation."""

REVIEWED = {
    ("GET", "/api/me"): {
        (
            "blizzard.hub.api.hub_level.hub_services",
            "ProblemHTTPException(problems.hub_store_unavailable_problem)",
        ),
        (
            "blizzard.hub.api.me.me",
            "problem_response(authentication_required_problem(instance=request.url.path, detail='not authenticated'))",
        ),
        # This response model is a conservative census candidate, not a refusal.
        (
            "blizzard.hub.api.me.me",
            "MeResponse(user_id=identity.user_id, username=identity.username, display_name=identity.display_name, role=role, permissions=[*sorted(permissions_for(role, identity.hub_admin))], assignable_roles=[*ASSIGNABLE_ROLES], tenants=tenants, hub_admin=identity.hub_admin)",
        ),
        (
            "blizzard.hub.api.problem_rendering.problem_response",
            "JSONResponse(content=content, status_code=_http_status(content, status_code), headers=headers, media_type=PROBLEM_MEDIA_TYPE)",
        ),
    },
}
