"""Versioned configuration documents reconcile explicit forge declarations and references."""

from pathlib import Path

import pytest

from tests.support import build_hub

pytestmark = pytest.mark.component

FORGE = {"slug": "enterprise", "kind": "github", "api_base": "https://api.example.test"}
REPO = {
    "slug": "code",
    "forge": "enterprise",
    "owner": "acme",
    "repo": "code",
    "base_branch": "main",
    "secret_name": "gh",
}
SOURCE = {"slug": "issues", "forge": "enterprise", "provider": "github", "locator": "acme/code", "secret": "gh"}


def test_v2_apply_dry_run_export_round_trip_and_noop(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, repositories=(), link=False)
    assert hub.client.post("/api/secrets", json={"name": "gh", "value": "v"}).status_code == 201
    document = {"version": 2, "forges": [FORGE], "repositories": [REPO], "work_sources": [SOURCE]}
    dry = hub.client.post("/api/config/apply?dry_run=true", json=document)
    assert dry.status_code == 200, dry.text
    assert dry.json()["apply_id"] is None
    assert [o["kind"] for o in dry.json()["outcomes"]] == ["forge", "work_source", "repository"]
    assert hub.services.forge_records.list_all(include_retired=True) == []
    applied = hub.client.post("/api/config/apply", json=document)
    assert applied.status_code == 200, applied.text
    assert applied.json()["warnings"] == []
    exported = hub.client.get("/api/config/export?version=2").json()
    assert exported["version"] == 2, exported
    assert exported["forges"] == [{**FORGE, "name": "enterprise", "web_base": None}]
    assert exported["repositories"][0]["forge"] == "enterprise"
    assert exported["work_sources"][0]["forge"] == "enterprise"
    assert "forge_api_url" not in exported["repositories"][0]
    assert "api_base" not in exported["work_sources"][0]
    again = hub.client.post("/api/config/apply", json=exported)
    assert again.status_code == 200, again.text
    assert all(o["op"] == "unchanged" for o in again.json()["outcomes"])
    legacy = hub.client.get("/api/config/export").json()
    assert legacy["version"] == 1 and "forges" not in legacy
    assert legacy["repositories"][0]["forge_api_url"] == FORGE["api_base"]


def test_v2_refusals_are_located_and_atomic(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, repositories=(), link=False)
    assert hub.client.post("/api/secrets", json={"name": "gh", "value": "v"}).status_code == 201
    base = {"version": 2, "forges": [FORGE], "repositories": [REPO]}
    missing = hub.client.post("/api/config/apply", json={**base, "repositories": [{**REPO, "forge": "unknown"}]})
    assert missing.status_code == 422
    assert missing.json()["detail"][0]["loc"] == ["body", "repositories", 0, "forge"]
    assert hub.services.forge_records.list_all(include_retired=True) == []
    assert hub.client.post("/api/config/apply", json=base).status_code == 200
    wrong_kind = hub.client.post("/api/config/apply", json={"version": 2, "forges": [{**FORGE, "kind": "other"}]})
    assert wrong_kind.status_code == 422
    assert wrong_kind.json()["detail"][0]["type"] == "forge_kind_immutable"
    forbidden = hub.client.post(
        "/api/config/apply",
        json={"version": 2, "repositories": [{**REPO, "forge_api_url": "https://api.example.test"}]},
    )
    assert forbidden.status_code == 422
    schema = hub.client.get("/api/config/schema/forges?version=2")
    assert schema.status_code == 200, schema.text
    assert hub.client.get("/api/config/schema/forges").status_code == 404


def test_v2_forge_edits_enable_and_former_slug_refusal(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, repositories=(), link=False)
    assert hub.client.post("/api/config/apply", json={"version": 2, "forges": [FORGE]}).status_code == 200
    renamed = hub.client.patch("/api/forges/enterprise", headers={"If-Match": "1"}, json={"slug": "new-enterprise"})
    assert renamed.status_code == 200, renamed.text
    former = hub.client.post("/api/config/apply", json={"version": 2, "forges": [FORGE]})
    assert former.status_code == 422
    assert former.json()["detail"][0]["loc"] == ["body", "forges", 0, "slug"]
    edited = hub.client.post(
        "/api/config/apply",
        json={"version": 2, "forges": [{**FORGE, "slug": "new-enterprise", "api_base": "https://new.example.test"}]},
    )
    assert edited.status_code == 200, edited.text
    assert [(o["kind"], o["op"]) for o in edited.json()["outcomes"]] == [("forge", "edit")]
    unchanged = hub.client.post(
        "/api/config/apply",
        json={"version": 2, "forges": [{**FORGE, "slug": "new-enterprise", "api_base": "https://new.example.test"}]},
    )
    assert unchanged.json()["outcomes"][0]["op"] == "unchanged"


def test_v2_can_enable_a_declared_retired_forge_before_references(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, repositories=(), link=False)
    assert hub.client.post("/api/secrets", json={"name": "gh", "value": "v"}).status_code == 201
    assert hub.client.post("/api/config/apply", json={"version": 2, "forges": [FORGE]}).status_code == 200
    assert hub.client.post("/api/forges/enterprise/retire", headers={"If-Match": "1"}).status_code == 200
    applied = hub.client.post("/api/config/apply", json={"version": 2, "forges": [FORGE], "repositories": [REPO]})
    assert applied.status_code == 200, applied.text
    assert [(o["kind"], o["op"]) for o in applied.json()["outcomes"]] == [("forge", "enable"), ("repository", "create")]
