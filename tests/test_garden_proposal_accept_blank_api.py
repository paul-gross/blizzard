"""Accepting a garden proposal into a blank work item is refused as unprocessable
(component tier)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.garden_proposals import GardenProposalOrigin
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from tests.support import build_hub, hub_store_connections

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def test_accept_minting_a_blank_body_is_422_and_mints_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    with hub.engine.begin() as conn:
        conn.execute(s.scopes.insert().values(slug="blizzard", description="", created_at=_NOW))
    GardenProposalStore(hub_store_connections(hub.engine)).create(
        "gprop_1",
        origin=GardenProposalOrigin.ROUTINE_RUN,
        routine_name="nightly",
        class_="fix-the-source",
        title="Author a docstring standard",
        body="the proposal's own body",
        findings=[],
        at=_NOW,
    )

    resp = hub.client.post("/api/garden-proposals/gprop_1/accept", json={"body": "   "})

    assert resp.status_code == 422, resp.text
    assert "body" in resp.json()["detail"]
    assert hub.client.get("/api/garden-proposals/gprop_1").json()["closure"] is None
