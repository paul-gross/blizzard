"""``GET /api/routines/{routine_id}/baselines`` (blizzard#399 D5, service tier) — the
shape a delivered run's baseline serves against a real hub daemon, and the 404 on an
unknown routine id. Reuses the garden delivery stack `test_finding_exits_service.py`
already stands up; FLEET_VIEW enforcement is the component-tier
`test_route_permission_matrix.py`'s own concern. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.test_acceptance_loop import REPO_NAME
from tests.service.support import service_gate
from tests.service.test_finding_exits_service import add_op, deliver, garden_stack, seed

pytestmark = [pytest.mark.service, service_gate]


def test_baselines_serves_the_swept_scopes_recorded_revision(tmp_path: Path) -> None:
    with garden_stack(tmp_path) as g:
        seed(g, 1)

        resp = g.hub.get(f"/api/routines/{g.routine_id}/baselines")

        assert resp.status_code == 200, resp.text
        (baseline,) = resp.json()
        assert baseline["scope_slug"] == "garden-svc"
        assert baseline["finding_set_id"].startswith("fins_")
        assert baseline["recorded_at"]
        assert baseline["repos"] == [{"repo": REPO_NAME, "revision": g.head, "landed_since": 0}]


def test_baselines_after_a_second_sweep_still_serves_one_newest_entry(tmp_path: Path) -> None:
    """D5 — one entry per scope, never one per sweep."""
    with garden_stack(tmp_path) as g:
        seed(g, 1)
        second = deliver(g, [add_op("src/app.py:second")])
        assert second.status_code == 200 and second.json()["outcome"] == "recorded", second.text

        baselines = g.hub.get(f"/api/routines/{g.routine_id}/baselines").json()

        assert len(baselines) == 1
        assert baselines[0]["scope_slug"] == "garden-svc"


def test_baselines_unknown_routine_is_404(tmp_path: Path) -> None:
    with garden_stack(tmp_path) as g:
        resp = g.hub.get("/api/routines/rtn_ghost/baselines")

        assert resp.status_code == 404
