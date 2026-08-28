"""Scope routes — create, list, read, edit, retire, and enable (blizzard#389, component
tier). Proves the HTTP surface end to end: a malformed slug 422s naming the rejected
value through both the create and edit routes, a re-create leaves the stored description
untouched, and the retire/enable brake is reversible and idempotent — the
``tests/test_graph_lifecycle_api.py`` shape."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub

pytestmark = pytest.mark.component


def test_create_mints_a_scope(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.post("/api/scopes", json={"slug": "blizzard", "description": "the repo"})

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["slug"] == "blizzard"
    assert body["description"] == "the repo"
    assert body["retired"] is False


def test_create_naming_an_existing_slug_leaves_its_description_unchanged(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    hub.client.post("/api/scopes", json={"slug": "blizzard", "description": "original"})

    resp = hub.client.post("/api/scopes", json={"slug": "blizzard", "description": "clobber attempt"})

    assert resp.status_code == 201, resp.text
    assert resp.json()["description"] == "original"


@pytest.mark.parametrize("slug", ["Blizzard", "blizzard_ops", "", "blizzard ops"])
def test_create_rejects_a_malformed_slug_naming_it(tmp_path: Path, slug: str) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.post("/api/scopes", json={"slug": slug, "description": ""})

    assert resp.status_code == 422, resp.text
    assert slug in resp.json()["detail"]


def test_edit_rejects_a_malformed_slug_naming_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.patch("/api/scopes/Not-A-Slug", json={"description": "x"})

    assert resp.status_code == 422, resp.text
    assert "Not-A-Slug" in resp.json()["detail"]


def test_list_renders_every_scope_including_retired(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    hub.client.post("/api/scopes", json={"slug": "alpha", "description": ""})
    hub.client.post("/api/scopes", json={"slug": "beta", "description": ""})
    hub.client.post("/api/scopes/beta/retire", json={"by": "operator"})

    rows = {row["slug"]: row for row in hub.client.get("/api/scopes").json()}

    assert rows["alpha"]["retired"] is False
    assert rows["beta"]["retired"] is True


def test_get_unknown_scope_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.get("/api/scopes/ghost")
    assert resp.status_code == 404


def test_edit_changes_the_description(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    hub.client.post("/api/scopes", json={"slug": "blizzard", "description": "old"})

    resp = hub.client.patch("/api/scopes/blizzard", json={"description": "new"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["description"] == "new"
    assert hub.client.get("/api/scopes/blizzard").json()["description"] == "new"


def test_edit_unknown_scope_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.patch("/api/scopes/ghost", json={"description": "x"})
    assert resp.status_code == 404


def test_retire_returns_202_and_the_view_reports_retired(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    hub.client.post("/api/scopes", json={"slug": "blizzard", "description": ""})

    resp = hub.client.post("/api/scopes/blizzard/retire", json={"by": "paul"})

    assert resp.status_code == 202, resp.text
    assert resp.json()["retired"] is True


def test_enable_reverses_a_retire(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    hub.client.post("/api/scopes", json={"slug": "blizzard", "description": ""})
    hub.client.post("/api/scopes/blizzard/retire", json={"by": "operator"})

    resp = hub.client.post("/api/scopes/blizzard/enable", json={"by": "operator"})

    assert resp.status_code == 202, resp.text
    assert resp.json()["retired"] is False
    assert hub.client.get("/api/scopes/blizzard").json()["retired"] is False


def test_retire_does_not_change_the_stored_description(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    hub.client.post("/api/scopes", json={"slug": "blizzard", "description": "the repo"})

    hub.client.post("/api/scopes/blizzard/retire", json={"by": "operator"})

    assert hub.client.get("/api/scopes/blizzard").json()["description"] == "the repo"


def test_a_second_retire_is_a_harmless_no_op(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    hub.client.post("/api/scopes", json={"slug": "blizzard", "description": ""})
    hub.client.post("/api/scopes/blizzard/retire", json={"by": "operator"})

    resp = hub.client.post("/api/scopes/blizzard/retire", json={"by": "operator"})

    assert resp.status_code == 202, resp.text
    assert resp.json()["retired"] is True


def test_retire_unknown_scope_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/scopes/ghost/retire", json={"by": "operator"})
    assert resp.status_code == 404


def test_enable_unknown_scope_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/scopes/ghost/enable", json={"by": "operator"})
    assert resp.status_code == 404


def test_retire_defaults_by_to_operator(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    hub.client.post("/api/scopes", json={"slug": "blizzard", "description": ""})

    resp = hub.client.post("/api/scopes/blizzard/retire", json={})

    assert resp.status_code == 202, resp.text
    assert resp.json()["retired"] is True
