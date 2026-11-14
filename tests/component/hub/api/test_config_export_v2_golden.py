"""The version-2 export's exact bytes (component tier, wired hub app): a store holding every version-2 section — a
repository and a work source acting with no credential among them — exports JSON and YAML equal, byte
for byte, to the goldens under ``tests/fixtures/config_export_v2``. ``BLIZZARD_CAPTURE_GOLDEN=1`` rewrites the goldens instead."""

from __future__ import annotations

import os
from pathlib import Path

from blizzard.hub.documents import codec_for_path
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.config.forges import ForgeSlug
from blizzard.hub.domain.config.repositories import RepositoryFields
from blizzard.hub.domain.config.work_sources import WorkSourceFields
from tests.support.scaffolding import HubHarness, build_hub

_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "config_export_v2"
_JSON = _FIXTURES / "export.json"
_YAML = _FIXTURES / "export.yaml"
_CTX = ChangeContext(actor="operator", door=Door.API)
_CAPTURE = os.environ.get("BLIZZARD_CAPTURE_GOLDEN") == "1"


def _graph(hub: HubHarness) -> str:
    return hub.services.default_graph.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    ).name


def _seeded(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path, repositories=(), link=False)
    for name in ("gh", "ops"):
        assert hub.client.post("/api/secrets", json={"name": name, "value": "v"}).status_code == 201
    document = {
        "version": 2,
        "urgencies": [{"slug": "background", "name": "Background", "color": "blue", "emoji": "🌙"}],
        "settings": {"default_urgency": "background"},
        "forges": [
            {"slug": "enterprise", "kind": "github", "api_base": "https://api.enterprise.test"},
            {"slug": "internal", "kind": "github", "api_base": "https://api.internal.test", "web_base": None},
        ],
        "secrets": ["gh", "ops"],
        "work_sources": [
            {"slug": "issues", "forge": "enterprise", "provider": "github", "locator": "acme/code", "secret": "gh"},
        ],
        "repositories": [
            {
                "slug": "code",
                "name": "Code",
                "forge": "enterprise",
                "owner": "acme",
                "repo": "code",
                "base_branch": "main",
                "secret_name": "gh",
                "landing_policy": "park",
                "allow_no_ci": True,
                "park_timeout_hours": 12,
            }
        ],
        "projects": [
            {
                "slug": "fleet",
                "name": "Fleet",
                "description": "the fleet",
                "color": "teal",
                "sources": ["issues", "public"],
                "repositories": ["code", "tools"],
                "landing_order": ["tools", "code"],
                "repository_branches": {"code": "trunk"},
            }
        ],
        "scopes": [{"slug": "core", "description": "the core", "project": "fleet"}],
        "routines": [
            {
                "slug": "sweep",
                "graph_name": _graph(hub),
                "default_scope_slug": "core",
                "default_urgency": "background",
                "scopes": ["core"],
                "project": "fleet",
            }
        ],
        "cadences": [
            {
                "slug": "evening",
                "project": "fleet",
                "spacing": "10d",
                "chores": [{"key": "nightly", "routine": "sweep", "urgency": "background"}],
            }
        ],
    }
    # The repository and the work source acting with no credential are written by the verbs that name none, as a
    # carried-over record is, on a forge an earlier apply declared.
    forges = hub.client.post("/api/config/apply", json={"version": 2, "forges": document["forges"]})
    assert forges.status_code == 200, forges.text
    forge = hub.services.forge_records.get_by_slug(ForgeSlug.parse("internal"))
    assert forge is not None
    authoring = hub.services.config_authoring
    authoring.create_repository(
        "tools",
        None,
        RepositoryFields(forge.forge_id, "acme", "tools", "main", "ops"),
        _CTX,
    )
    authoring.create_work_source(
        "public", None, WorkSourceFields("github", "acme/open", forge.forge_id, annotate=False, secret="ops"), _CTX
    )
    applied = hub.client.post("/api/config/apply", json=document)
    assert applied.status_code == 200, applied.text
    return hub


def _pinned(path: Path, actual: bytes) -> None:
    if _CAPTURE:
        path.write_bytes(actual)
    assert actual == path.read_bytes()


def test_the_v2_export_is_byte_identical_to_its_golden(tmp_path: Path) -> None:
    hub = _seeded(tmp_path)
    tools = hub.services.repository_records.resolve("tools")
    public = hub.services.work_source_records.resolve("public")
    assert tools is not None and tools.fields.credential_id is None
    assert public is not None and public.fields.credential_id is None

    exported = hub.client.get("/api/config/export")

    assert exported.status_code == 200, exported.text
    assert exported.content == hub.client.get("/api/config/export?version=2").content
    codec = codec_for_path("export.yaml")
    assert codec is not None
    _pinned(_JSON, exported.content)
    _pinned(_YAML, codec.encode(exported.json()))


def test_reapplying_the_v2_export_over_records_acting_with_no_credential_settles_unchanged(tmp_path: Path) -> None:
    hub = _seeded(tmp_path)
    before = hub.client.get("/api/config/changes").json()["total"]

    again = hub.client.post(
        "/api/config/apply", content=_JSON.read_bytes(), headers={"Content-Type": "application/json"}
    )

    assert again.status_code == 200, again.text
    assert {o["op"] for o in again.json()["outcomes"]} == {"unchanged"}
    assert hub.client.get("/api/config/changes").json()["total"] == before
    tools = hub.services.repository_records.resolve("tools")
    public = hub.services.work_source_records.resolve("public")
    assert tools is not None and tools.fields.credential_id is None
    assert public is not None and public.fields.credential_id is None
