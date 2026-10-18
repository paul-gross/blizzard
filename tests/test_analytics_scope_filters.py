"""Value-level conversion of analytics scope filters to event query criteria."""

from datetime import UTC, datetime

import pytest

from blizzard.hub.api.analytics import EventScopeFilters, ScopeFilters
from blizzard.hub.domain.projects.model import ProjectId

pytestmark = pytest.mark.unit


def test_event_criteria_normalizes_non_default_scope_instants() -> None:
    """Naive scope bounds become UTC-aware in event criteria, as in the shared scope conversion."""
    since = datetime(2026, 8, 12, 9, 30)
    until = datetime(2026, 8, 13, 10, 45)
    scope = ScopeFilters("gr_1", "ws_1", since, until, ProjectId("pj_1"))
    filters = EventScopeFilters(scope, "prior", "claude_code", "1.0", "model-a", "high")

    criteria = filters.criteria
    normalized = scope.normalized()

    assert (criteria.graph_id, criteria.source, criteria.project) == (
        normalized.graph_id,
        normalized.source,
        normalized.project,
    )
    assert (criteria.since, criteria.until) == (since.replace(tzinfo=UTC), until.replace(tzinfo=UTC))
    assert (criteria.since, criteria.until) == (normalized.since, normalized.until)
    assert (criteria.harness_id, criteria.harness_version, criteria.model, criteria.effort) == (
        "claude_code",
        "1.0",
        "model-a",
        "high",
    )
