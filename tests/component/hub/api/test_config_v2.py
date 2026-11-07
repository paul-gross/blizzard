"""Versioned configuration documents reconcile explicit forge declarations and references."""

from pathlib import Path

import pytest

from blizzard.wire.config import ConfigDocumentInvalidProblem
from blizzard.wire.problem import CONFIG_DOCUMENT_INVALID
from tests.support.refusal_observation import app_spec, declared_refusal_mismatches
from tests.support.scaffolding import build_hub

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
    assert hub.client.get("/api/config/export").json() == exported


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
    assert hub.client.get("/api/config/schema/forges").json() == schema.json()


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


@pytest.mark.parametrize("version", [1, None, 3, "2"])
def test_a_document_of_any_version_but_2_is_refused_naming_version_2(tmp_path: Path, version: object) -> None:
    hub = build_hub(tmp_path, repositories=(), link=False)
    document: dict[str, object] = {"forges": [FORGE]}
    if version is not None:
        document["version"] = version
    refused = hub.client.post("/api/config/apply", json=document)
    assert refused.status_code == 422, refused.text
    (detail,) = refused.json()["detail"]
    assert detail["loc"] == ["body", "version"]
    assert detail["type"] == "config_version_retired"
    assert "version 2" in detail["msg"]
    assert hub.services.forge_records.list_all(include_retired=True) == []


@pytest.mark.parametrize("path", ["/api/config/export", "/api/config/schema/repositories"])
def test_version_1_of_the_export_and_a_schema_is_refused_naming_version_2(tmp_path: Path, path: str) -> None:
    hub = build_hub(tmp_path, repositories=(), link=False)
    refused = hub.client.get(path, params={"version": 1})
    declared = path.replace("repositories", "{kind}")
    spec = app_spec(hub.client)
    assert declared_refusal_mismatches(spec, refused, "get", declared, 422, ConfigDocumentInvalidProblem) == []
    assert refused.json()["type"] == CONFIG_DOCUMENT_INVALID
    (detail,) = refused.json()["detail"]
    assert (detail["loc"], detail["type"]) == (["query", "version"], "config_version_retired")
    assert "version 2" in detail["msg"]


def test_the_export_and_each_schema_answer_version_2_by_default(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, repositories=(), link=False)
    assert hub.client.get("/api/config/export").json()["version"] == 2
    repositories = hub.client.get("/api/config/schema/repositories").json()
    assert "forge" in repositories["properties"] and "forge_api_url" not in repositories["properties"]
    sources = hub.client.get("/api/config/schema/work-sources").json()
    assert "forge" in sources["properties"] and "api_base" not in sources["properties"]
