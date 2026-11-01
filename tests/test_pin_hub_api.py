"""Pinning tests for hub decisions that were previously defended by comment alone
(``bzh:mutation-review-selection``).

Each test exists because a long comment argued for a decision no assertion covered;
the comment at each site now points back at the test that fails on revert."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from blizzard.auth_core import Permission
from blizzard.hub.api.auth_session import require
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.config import HubConfig

pytestmark = [pytest.mark.unit]


def test_require_reports_the_store_unwired_with_no_store_wired() -> None:
    """A ``require()``-gated route serves a tenant, and a store-free app has none to resolve: under
    the default ``auth.mode = "none"`` the gate answers 503, never the implicit operator unscoped."""
    app = FastAPI()
    app.state.config = HubConfig(root=Path("."), db_url="sqlite://")  # `auth.mode` defaults to "none"
    app.state.services = None  # store-free, exactly as the export/unit app is built

    @app.get("/_pin_probe")
    def _probe(identity: ResolvedIdentity = Depends(require(Permission.FLEET_VIEW))) -> dict[str, str]:
        return {"username": identity.username}

    with TestClient(app) as client:
        resp = client.get("/_pin_probe")

    assert resp.status_code == 503, resp.text
