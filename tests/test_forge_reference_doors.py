"""The repository and work-source doors name a forge by ``forge`` or by an old address field: the coded refusals, the
``Deprecation`` header and ``warnings`` an old field answers, and the ``config-old-field-used`` event it records
(component tier, over the wired app)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from blizzard.hub.api.forge_warnings import DEPRECATION_HEADER
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeFields
from blizzard.hub.store import schema
from tests.support import FIXTURE_FORGE_SECRET, HubHarness, build_hub, ensure_fixture_secret, hub_store_connections

pytestmark = pytest.mark.component

_GHE = "https://ghe.acme.internal/api/v3"
_OPERATOR = ChangeContext(actor="alice", door=Door.CLI)
_HEADER, _DEPRECATED = DEPRECATION_HEADER


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    harness = build_hub(tmp_path, repositories=(), link=False)
    ensure_fixture_secret(harness.client)
    return harness


def _ghe(hub: HubHarness) -> ConfiguredForge:
    return hub.services.config_authoring.create_forge("ghe", "Acme GHE", "github", ForgeFields(_GHE, None), _OPERATOR)


def _repository(slug: str = "demo", **naming: str) -> dict[str, str]:
    return {
        "slug": slug,
        **naming,
        "owner": "acme",
        "repo": slug,
        "base_branch": "main",
        "secret_name": FIXTURE_FORGE_SECRET,
    }


def _source(slug: str = "issues", **naming: Any) -> dict[str, Any]:
    return {"slug": slug, "provider": "github", "locator": f"acme/{slug}", "secret": FIXTURE_FORGE_SECRET, **naming}


def _refusal(response: Any) -> tuple[str, str]:
    """The field and code of a coded 422."""
    assert response.status_code == 422, response.text
    [detail] = response.json()["detail"]
    return detail["loc"][-1], detail["type"]


def _old_field_events(hub: HubHarness) -> list[dict[str, Any]]:
    """Every ``config-old-field-used`` event, oldest first."""
    listed = hub.client.get("/api/events", params={"severity": "warning"})
    assert listed.status_code == 200, listed.text
    return [
        {"severity": event["severity"], **event["detail"]}
        for event in reversed(listed.json()["events"])
        if event["kind"] == "config-old-field-used"
    ]


def test_a_repository_named_by_forge_answers_no_warning_and_reads_its_forge(hub: HubHarness) -> None:
    forge = _ghe(hub)
    created = hub.client.post("/api/repositories", json=_repository(forge="ghe"))
    assert created.status_code == 201, created.text
    body = created.json()
    assert _HEADER not in created.headers
    assert (body["forge_id"], body["forge_slug"], body["forge_name"]) == (forge.forge_id, "ghe", "Acme GHE")
    assert (body["forge_api_url"], body["warnings"]) == (_GHE, [])
    assert _old_field_events(hub) == []


def test_an_old_field_alone_makes_the_forge_and_answers_the_header_a_warning_and_an_event(hub: HubHarness) -> None:
    created = hub.client.post("/api/repositories", json=_repository(forge_api_url="https://GHE.acme.internal/api/v3/"))
    assert created.status_code == 201, created.text
    body = created.json()
    assert created.headers[_HEADER] == _DEPRECATED
    assert body["forge_slug"] == "ghe-acme-internal"
    [warning] = body["warnings"]
    assert (warning["field"], warning["forge_id"], warning["forge_slug"]) == (
        "forge_api_url",
        body["forge_id"],
        "ghe-acme-internal",
    )
    [event] = _old_field_events(hub)
    assert event == {
        "severity": "warning",
        "record": "repository demo",
        "field": "forge_api_url",
        "forge_id": body["forge_id"],
        "forge_slug": "ghe-acme-internal",
    }
    again = hub.client.post("/api/repositories", json=_repository("other", forge_api_url=_GHE))
    assert again.json()["forge_id"] == body["forge_id"]


def test_a_repository_naming_no_forge_is_refused_forge_required(hub: HubHarness) -> None:
    assert _refusal(hub.client.post("/api/repositories", json=_repository())) == ("forge", "forge_required")


def test_an_old_field_disagreeing_with_the_named_forge_is_refused_forge_conflict(hub: HubHarness) -> None:
    _ghe(hub)
    refused = hub.client.post(
        "/api/repositories", json=_repository(forge="ghe", forge_api_url="https://other.example/api/v3")
    )
    assert _refusal(refused) == ("forge_api_url", "forge_conflict")
    agreeing = hub.client.post("/api/repositories", json=_repository(forge="ghe", forge_api_url=_GHE))
    assert agreeing.status_code == 201, agreeing.text
    assert [w["field"] for w in agreeing.json()["warnings"]] == ["forge_api_url"]


def test_an_unknown_forge_and_an_origin_a_retired_forge_holds_are_refused_forge_unavailable(hub: HubHarness) -> None:
    unknown = hub.client.post("/api/repositories", json=_repository(forge="nowhere"))
    assert _refusal(unknown) == ("forge", "forge_unavailable")
    hub.services.config_authoring.retire_forge(_ghe(hub), _OPERATOR)
    named = hub.client.post("/api/repositories", json=_repository(forge="ghe"))
    assert _refusal(named) == ("forge", "forge_unavailable")
    held = hub.client.post("/api/work-sources", json=_source(api_base=_GHE))
    assert _refusal(held) == ("api_base", "forge_unavailable")
    assert "ghe" in held.json()["detail"][0]["msg"]


def test_a_forge_of_another_kind_than_the_provider_is_refused_forge_kind_mismatch(hub: HubHarness) -> None:
    forge = _ghe(hub)
    # No second kind exists yet, so the mismatch is planted in the store.
    scoped = hub_store_connections(hub.engine)
    with scoped.write("plant") as conn:
        conn.execute(
            scoped.update(schema.forges).where(schema.forges.c.forge_id == forge.forge_id).values(kind="gitlab")
        )
    refused = hub.client.post("/api/work-sources", json=_source(forge="ghe"))
    assert _refusal(refused) == ("forge", "forge_kind_mismatch")


def test_a_work_source_created_naming_nothing_is_on_github_with_a_warning(hub: HubHarness) -> None:
    created = hub.client.post("/api/work-sources", json=_source())
    assert created.status_code == 201, created.text
    body = created.json()
    assert created.headers[_HEADER] == _DEPRECATED
    assert (body["forge_slug"], body["api_base"], body["web_base"]) == (
        "github",
        "https://api.github.com",
        "https://github.com",
    )
    assert [w["field"] for w in body["warnings"]] == ["api_base"]


def test_a_work_source_edit_naming_nothing_keeps_its_forge_and_a_cleared_api_base_moves_it_to_github(
    hub: HubHarness,
) -> None:
    _ghe(hub)
    assert hub.client.post("/api/work-sources", json=_source(forge="ghe")).status_code == 201
    kept = hub.client.patch("/api/work-sources/issues", json={"annotate": True})
    assert kept.status_code == 200, kept.text
    assert (kept.json()["forge_slug"], kept.json()["warnings"], _HEADER in kept.headers) == ("ghe", [], False)
    moved = hub.client.patch("/api/work-sources/issues", json={"api_base": None})
    assert moved.status_code == 200, moved.text
    assert moved.json()["forge_slug"] == "github"
    assert [(w["field"], w["forge_slug"]) for w in moved.json()["warnings"]] == [("api_base", "github")]
    assert moved.headers[_HEADER] == _DEPRECATED


def test_a_forge_sent_null_is_refused(hub: HubHarness) -> None:
    _ghe(hub)
    assert hub.client.post("/api/repositories", json=_repository(forge="ghe")).status_code == 201
    refused = hub.client.patch("/api/repositories/demo", json={"forge": None})
    assert _refusal(refused) == ("forge", "value_error")


def test_a_config_apply_answers_a_warning_per_old_field_and_records_events_only_when_it_commits(
    hub: HubHarness,
) -> None:
    document = {
        "version": 1,
        "work_sources": [_source(api_base=_GHE, web_base=None)],
        "repositories": [_repository(forge_api_url=_GHE)],
    }
    dry = hub.client.post("/api/config/apply", params={"dry_run": "true"}, json=document)
    assert dry.status_code == 200, dry.text
    assert [w["field"] for w in dry.json()["warnings"]] == ["api_base", "web_base", "forge_api_url"]
    assert dry.headers[_HEADER] == _DEPRECATED
    assert _old_field_events(hub) == []
    applied = hub.client.post("/api/config/apply", json=document)
    assert applied.status_code == 200, applied.text
    warnings = applied.json()["warnings"]
    assert [w["field"] for w in warnings] == ["api_base", "web_base", "forge_api_url"]
    assert {w["forge_slug"] for w in warnings} == {"ghe-acme-internal"}
    assert [e["field"] for e in _old_field_events(hub)] == ["api_base", "web_base", "forge_api_url"]


def test_an_apply_naming_no_address_answers_no_warning(hub: HubHarness) -> None:
    assert hub.client.post("/api/work-sources", json=_source(api_base=_GHE)).status_code == 201
    applied = hub.client.post("/api/config/apply", json={"version": 1, "work_sources": [_source(annotate=True)]})
    assert applied.status_code == 200, applied.text
    assert (applied.json()["warnings"], _HEADER in applied.headers) == ([], False)
    assert hub.client.get("/api/work-sources/issues").json()["forge_slug"] == "ghe-acme-internal"


def test_the_deprecation_header_is_a_structured_date() -> None:
    assert (_HEADER, _DEPRECATED) == ("Deprecation", "@1792886400")


def test_an_apply_naming_a_new_origin_bare_then_with_a_web_origin_makes_one_forge_with_it(hub: HubHarness) -> None:
    document = {
        "version": 1,
        "work_sources": [
            _source("bare", api_base="https://api.acme.example"),
            _source("declared", api_base="https://api.acme.example", web_base="https://code.acme.example"),
        ],
    }
    applied = hub.client.post("/api/config/apply", json=document)
    assert applied.status_code == 200, applied.text
    sources = [hub.client.get(f"/api/work-sources/{slug}").json() for slug in ("bare", "declared")]
    assert {(s["forge_slug"], s["web_base"]) for s in sources} == {("code-acme-example", "https://code.acme.example")}


def test_a_refused_create_naming_a_new_origin_leaves_no_forge(hub: HubHarness) -> None:
    refused = hub.client.post("/api/repositories", json={**_repository(forge_api_url=_GHE), "secret_name": "missing"})
    assert refused.status_code == 422, refused.text
    scoped = hub_store_connections(hub.engine)
    with scoped.read("forges") as conn:
        origins = list(conn.execute(scoped.select(schema.forges, schema.forges.c.api_base)).scalars())
    assert _GHE not in origins
    assert _old_field_events(hub) == []
