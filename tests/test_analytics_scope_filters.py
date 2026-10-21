"""Value-level conversion of analytics scope filters to event query criteria."""

from datetime import UTC, datetime

import pytest

from blizzard.hub.api.analytics import EventScopeFilters, OperationalScopeFilters, ScopeFilters, operational_criteria
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


def test_operational_criteria_carry_the_scope_and_the_usage_provenance_but_not_effort() -> None:
    since = datetime(2026, 8, 12, 9, 30)
    scope = ScopeFilters("gr_1", "ws_1", since, None, ProjectId("pj_1"))
    filters = OperationalScopeFilters(scope, "model-a", "claude_code", "1.0")

    criteria = filters.criteria

    assert (criteria.graph_id, criteria.source, criteria.project) == ("gr_1", "ws_1", "pj_1")
    assert criteria.since == since.replace(tzinfo=UTC)
    assert (criteria.model, criteria.harness_id, criteria.harness_version) == ("model-a", "claude_code", "1.0")
    assert not hasattr(criteria, "effort")


def test_the_shared_scope_reading_names_no_provenance() -> None:
    criteria = operational_criteria(ScopeFilters(None, None, None, None))

    assert (criteria.model, criteria.harness_id, criteria.harness_version) == (None, None, None)
