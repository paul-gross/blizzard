"""Garden-proposal routes — the read half (blizzard#390, component tier).

Seeded straight through ``GardenProposalStore`` since no route writes a proposal yet
(passing/accepting is a sibling issue), the ``tests/test_finding_api.py`` shape."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from tests.support import build_hub

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _seed(hub) -> None:  # type: ignore[no-untyped-def]
    with hub.engine.begin() as conn:
        conn.execute(insert(s.scopes).values(slug="blizzard", description="", created_at=_NOW))
    FindingStore(hub.engine).add(
        "fin_1",
        routine_name="nightly",
        scope_slug="blizzard",
        class_="stale-docstring",
        locus="a.py:1",
        summary="s1",
        introduced=None,
        at=_NOW,
    )


def test_list_renders_every_proposal_newest_first(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)
    proposals = GardenProposalStore(hub.engine)
    proposals.create(
        "gprop_old", routine_name="nightly", class_="c", title="old", body="b", findings=["fin_1"], at=_NOW
    )
    proposals.create(
        "gprop_new",
        routine_name="nightly",
        class_="c",
        title="new",
        body="b",
        findings=["fin_1"],
        at=_NOW.replace(hour=13),
    )

    resp = hub.client.get("/api/garden-proposals")

    assert resp.status_code == 200, resp.text
    assert [row["proposal_id"] for row in resp.json()] == ["gprop_new", "gprop_old"]


def test_get_renders_one_proposal(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)
    GardenProposalStore(hub.engine).create(
        "gprop_1",
        routine_name="nightly",
        class_="fix-the-source",
        title="Author a docstring standard",
        body="the case",
        findings=["fin_1"],
        at=_NOW,
    )

    resp = hub.client.get("/api/garden-proposals/gprop_1")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["class"] == "fix-the-source"
    assert body["findings"] == ["fin_1"]


def test_get_unknown_id_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.get("/api/garden-proposals/gprop_ghost")

    assert resp.status_code == 404, resp.text
