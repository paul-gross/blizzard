"""The advanced-development-workflow ``plan-review`` node's choice table (unit tier)."""

from __future__ import annotations

import pytest

from blizzard.hub.graphs import PACKAGED

pytestmark = pytest.mark.unit


def test_plan_review_routes_to_build_plan_or_escalation() -> None:
    plan_review = PACKAGED.named("advanced-development-workflow").doc.node("plan-review")
    assert plan_review is not None and plan_review.judgement is not None
    routes = {c.name: c.to for c in plan_review.judgement.choices}
    assert routes == {"acceptable": "build", "must-fix": "plan", "unfulfillable": "escalate"}
