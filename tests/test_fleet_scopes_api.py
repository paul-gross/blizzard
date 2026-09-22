"""``GET /api/fleet/scopes`` — the deployment's scope vocabulary, worker-facing
(blizzard#582 D2, component tier). Reuses ``scopes.py``'s own ``scope_view`` projection,
the ``test_fleet_garden_findings_api.py`` shape."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub

pytestmark = pytest.mark.component


def test_empty_scope_registry_is_an_empty_list(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.get("/api/fleet/scopes")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_lists_every_scope_marked_retired_or_not(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    hub.client.post("/api/scopes", json={"slug": "blizzard", "description": "the platform"})
    hub.client.post("/api/scopes", json={"slug": "other", "description": ""})
    hub.client.post("/api/scopes/other/retire", json={"by": "u_1"})

    resp = hub.client.get("/api/fleet/scopes")

    assert resp.status_code == 200, resp.text
    by_slug = {row["slug"]: row for row in resp.json()}
    assert by_slug["blizzard"]["retired"] is False
    assert by_slug["blizzard"]["description"] == "the platform"
    assert by_slug["other"]["retired"] is True


def test_matches_the_operator_scopes_read(tmp_path: Path) -> None:
    """The fleet route reuses the same projection the operator's own `GET /api/scopes`
    reads through, rather than restating it."""
    hub = build_hub(tmp_path)
    hub.client.post("/api/scopes", json={"slug": "blizzard", "description": "d"})

    fleet = hub.client.get("/api/fleet/scopes")
    operator = hub.client.get("/api/scopes")

    assert fleet.status_code == 200 and operator.status_code == 200
    assert fleet.json() == operator.json()
