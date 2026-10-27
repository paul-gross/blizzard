"""Forge record HTTP and CLI surfaces over a tenant-scoped hub."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.auth_core import Role
from tests.support import (
    FIXTURE_FORGE_SECRET,
    HubHarness,
    build_hub,
    count_queries,
    ensure_fixture_secret,
    seed_session,
    seed_user,
)

pytestmark = pytest.mark.component


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    harness = build_hub(tmp_path, repositories=(), link=False)
    ensure_fixture_secret(harness.client)
    return harness


def _create(hub: HubHarness, slug: str = "ghe", api_base: str = "https://ghe.example/api/v3") -> dict:
    response = hub.client.post("/api/forges", json={"slug": slug, "kind": "github", "api_base": api_base})
    assert response.status_code == 201, response.text
    return response.json()


def test_create_list_detail_rename_and_revision(hub: HubHarness) -> None:
    created = _create(hub)
    assert (created["revision"], created["web_origin"], created["repositories"]) == (1, "https://ghe.example", [])
    assert hub.client.get("/api/forges").json()["forges"][0]["forge_id"] == created["forge_id"]
    assert hub.client.get(f"/api/forges/{created['forge_id']}").json() == created
    edited = hub.client.patch("/api/forges/ghe", json={"slug": "new-ghe", "web_base": "https://code.example"})
    assert edited.status_code == 200, edited.text
    assert (edited.json()["revision"], edited.json()["web_origin"]) == (2, "https://code.example")
    assert hub.client.get("/api/forges/ghe").json()["slug"] == "new-ghe"
    stale = hub.client.patch("/api/forges/ghe", json={}, headers={"If-Match": "1"})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "revision_conflict"
    assert stale.json()["detail"]["current"] == 2
    assert hub.client.patch("/api/forges/ghe", json={}).json()["revision"] == 2
    assert hub.client.get("/api/forges/unknown").status_code == 404
    assert hub.client.patch("/api/forges/ghe", json={"web_base": None}).json()["web_origin"] == "https://ghe.example"


def test_coded_fields_collisions_and_retirement_refs(hub: HubHarness) -> None:
    created = _create(hub)
    assert (
        hub.client.post(
            "/api/forges", json={"slug": "ghe", "kind": "github", "api_base": "https://other.example"}
        ).json()["detail"]["code"]
        == "forge_slug_taken"
    )
    assert (
        hub.client.post(
            "/api/forges", json={"slug": "other", "kind": "github", "api_base": "https://ghe.example/api/v3/"}
        ).json()["detail"]["code"]
        == "forge_origin_taken"
    )
    refused = hub.client.patch("/api/forges/ghe", json={"api_base": None})
    assert (refused.status_code, refused.json()["detail"][0]["loc"]) == (422, ["body", "api_base"])
    repository = hub.client.post(
        "/api/repositories",
        json={
            "slug": "demo",
            "forge": "ghe",
            "owner": "acme",
            "repo": "demo",
            "base_branch": "main",
            "secret_name": FIXTURE_FORGE_SECRET,
        },
    ).json()
    source = hub.client.post(
        "/api/work-sources",
        json={
            "slug": "issues",
            "forge": "ghe",
            "provider": "github",
            "locator": "acme/issues",
            "secret": FIXTURE_FORGE_SECRET,
        },
    )
    assert source.status_code == 201, source.text
    detail = hub.client.get("/api/forges/ghe").json()
    assert detail["repositories"] == [{"kind": "repository", "key": repository["repository_id"], "label": "demo"}]
    assert detail["work_sources"] == [
        {"kind": "work_source", "key": source.json()["work_source_id"], "label": "issues"}
    ]
    conflict = hub.client.post("/api/forges/ghe/retire")
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "forge_in_use"
    assert conflict.json()["detail"]["referrers"] == detail["repositories"] + detail["work_sources"]
    assert hub.client.post("/api/repositories/demo/retire").status_code == 200
    assert hub.client.post("/api/work-sources/issues/retire").status_code == 200
    assert hub.client.post("/api/forges/ghe/retire").json()["retired"] is True
    assert hub.client.get("/api/forges").json()["forges"] == []
    assert (
        hub.client.get("/api/forges", params={"include_retired": True}).json()["forges"][0]["forge_id"]
        == created["forge_id"]
    )
    assert hub.client.post("/api/forges/ghe/enable").json()["retired"] is False


def test_forge_diff_labels_are_current_and_missing_ids_remain_null(hub: HubHarness) -> None:
    first = _create(hub)
    second = _create(hub, "other", "https://other.example")
    repository = hub.client.post(
        "/api/repositories",
        json={
            "slug": "demo",
            "forge": "ghe",
            "owner": "acme",
            "repo": "demo",
            "base_branch": "main",
            "secret_name": FIXTURE_FORGE_SECRET,
        },
    )
    assert repository.status_code == 201, repository.text
    assert hub.client.patch("/api/repositories/demo", json={"forge": "other"}).status_code == 200
    assert hub.client.patch("/api/forges/other", json={"slug": "renamed", "name": "Second Forge"}).status_code == 200
    changes = hub.client.get("/api/config/changes", params={"record_kind": "repository"}).json()["changes"]
    forge_diff = next(diff for diff in changes[0]["diff"] if diff["field"] == "forge")
    assert (forge_diff["old"], forge_diff["old_label"], forge_diff["new"], forge_diff["new_label"]) == (
        first["forge_id"],
        "ghe · ghe",
        second["forge_id"],
        "renamed · Second Forge",
    )


def test_change_page_reads_forge_labels_in_one_batch(hub: HubHarness) -> None:
    _create(hub)

    def read():
        return hub.client.get("/api/config/changes", params={"record_kind": "forge", "limit": 100})

    one = count_queries(hub.engine, read)
    for index in range(5):
        _create(hub, f"ghe-{index}", f"https://ghe-{index}.example")
    assert count_queries(hub.engine, read) == one


def test_forge_reads_and_writes_require_their_permissions(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth", repositories=(), link=False)
    admin = {"Authorization": f"Bearer {seed_session(hub, seed_user(hub, username='ada', role=Role.ADMIN))}"}
    contributor = {
        "Authorization": f"Bearer {seed_session(hub, seed_user(hub, username='con', role=Role.CONTRIBUTOR))}"
    }
    guest = {"Authorization": f"Bearer {seed_session(hub, seed_user(hub, username='gus', role=Role.GUEST))}"}
    body = {"slug": "ghe", "kind": "github", "api_base": "https://ghe.example"}
    assert hub.client.post("/api/forges", json=body).status_code == 401
    assert hub.client.post("/api/forges", json=body, headers=contributor).status_code == 403
    assert hub.client.post("/api/forges", json=body, headers=admin).status_code == 201
    for path in ("/api/forges", "/api/forges/ghe"):
        assert hub.client.get(path, headers=guest).status_code == 200
        assert hub.client.get(path).status_code == 401
    assert hub.client.patch("/api/forges/ghe", json={}, headers=contributor).status_code == 403
    for verb in ("retire", "enable"):
        assert hub.client.post(f"/api/forges/ghe/{verb}", headers=contributor).status_code == 403
