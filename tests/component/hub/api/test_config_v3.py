"""A version-3 configuration document (component tier, wired hub app): it declares credentials and names one on each
work source and repository, applies in one unit beside version 2, exports back to itself, and serves its own schemas;
each refusal of a credential entry or of a referrer naming one leaves the store unchanged."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from blizzard.hub.store import schema as s
from blizzard.wire.config import CONFIG_DOCUMENT_INVALID, CredentialEntry
from tests.support.scaffolding import HubHarness, build_hub, hub_scoped

_FORGE = {
    "slug": "enterprise",
    "name": "enterprise",
    "kind": "github",
    "api_base": "https://api.enterprise.test",
    "web_base": None,
}
_OTHER = {"slug": "other", "name": "other", "kind": "github", "api_base": "https://api.other.test"}
_CREDENTIAL = {
    "slug": "deploy",
    "name": "deploy",
    "forge": "enterprise",
    "binding": "github.token",
    "secret": "gh",
    "settings": {"variant": "classic"},
}
_REPO = {
    "slug": "code",
    "name": "code",
    "forge": "enterprise",
    "owner": "acme",
    "repo": "code",
    "base_branch": "main",
    "credential": "deploy",
    "landing_policy": "merge-on-checks",
    "allow_no_ci": False,
    "park_timeout_hours": None,
}
_SOURCE = {
    "slug": "issues",
    "name": "issues",
    "provider": "github",
    "locator": "acme/code",
    "forge": "enterprise",
    "annotate": False,
    "credential": "deploy",
}


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    harness = build_hub(tmp_path, repositories=(), link=False)
    for name in ("gh", "ops"):
        assert harness.client.post("/api/secrets", json={"name": name, "value": "v"}).status_code == 201
    return harness


def _apply(hub: HubHarness, sections: dict[str, Any], *, dry_run: bool = False) -> Any:
    return hub.client.post("/api/config/apply", params={"dry_run": dry_run}, json={"version": 3, **sections})


def _document() -> dict[str, Any]:
    return {
        "forges": [_FORGE],
        "secrets": ["gh", "ops"],
        "credentials": [_CREDENTIAL],
        "work_sources": [_SOURCE],
        "repositories": [_REPO],
    }


def _change_rows(hub: HubHarness) -> list[tuple[str, str, str | None]]:
    with hub.engine.connect() as conn:
        rows = conn.execute(
            hub_scoped(
                sa.select(s.config_changes.c.record_kind, s.config_changes.c.op, s.config_changes.c.apply_id).order_by(
                    s.config_changes.c.id
                )
            )
        ).all()
    return [(row.record_kind, row.op, row.apply_id) for row in rows]


def _credential(hub: HubHarness, slug: str = "deploy") -> dict[str, Any]:
    response = hub.client.get(f"/api/credentials/{slug}")
    assert response.status_code == 200, response.text
    return response.json()


def test_a_document_declaring_a_credential_and_its_referrers_applies_in_one_unit(hub: HubHarness) -> None:
    applied = _apply(hub, _document())

    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert [(o["kind"], o["op"]) for o in body["outcomes"]] == [
        ("forge", "create"),
        ("credential", "create"),
        ("work_source", "create"),
        ("repository", "create"),
    ]
    rows = [row for row in _change_rows(hub) if row[2] is not None]
    assert {apply_id for _, _, apply_id in rows} == {body["apply_id"]}
    assert sorted(kind for kind, _, _ in rows) == ["credential", "forge", "repository", "work_source"]
    credential = _credential(hub)
    assert (credential["forge_slug"], credential["authority"], credential["settings"]) == (
        "enterprise",
        "secret/gh",
        {"variant": "classic"},
    )
    repository = hub.client.get("/api/repositories/code").json()
    source = hub.client.get("/api/work-sources/issues").json()
    assert repository["credential_id"] == source["credential_id"] == credential["credential_id"]
    assert (repository["secret_name"], source["secret"]) == ("gh", "gh")
    assert {(r["kind"], r["label"]) for r in credential["referrers"]} == {
        ("repository", "code"),
        ("work_source", "issues"),
    }


def test_the_version_3_export_is_the_document_and_reapplies_unchanged(hub: HubHarness) -> None:
    assert _apply(hub, _document()).status_code == 200

    exported = hub.client.get("/api/config/export", params={"version": 3})

    assert exported.status_code == 200, exported.text
    document = exported.json()
    assert document["version"] == 3
    for section, entries in _document().items():
        assert document[section] == entries, section
    again = _apply(hub, {k: v for k, v in document.items() if k != "version"})
    assert again.status_code == 200, again.text
    assert {o["op"] for o in again.json()["outcomes"]} == {"unchanged"}


def test_a_dry_run_writes_nothing_and_answers_the_outcomes_of_the_apply(hub: HubHarness) -> None:
    before = _change_rows(hub)
    dry = _apply(hub, _document(), dry_run=True)

    assert dry.status_code == 200, dry.text
    assert _change_rows(hub) == before
    real = _apply(hub, _document())

    def shown(response: Any) -> list[tuple[str, str, str, list[str]]]:
        return [(o["kind"], o["key"], o["op"], [d["field"] for d in o["diff"]]) for o in response.json()["outcomes"]]

    assert shown(dry) == shown(real)


@pytest.mark.parametrize("section", ["repositories", "work_sources"])
def test_a_referrer_naming_a_credential_of_another_forge_is_refused_and_nothing_is_written(
    hub: HubHarness, section: str
) -> None:
    other = {**_CREDENTIAL, "slug": "elsewhere", "name": "elsewhere", "forge": "other"}
    entry = {**(_REPO if section == "repositories" else _SOURCE), "credential": "elsewhere"}
    document = {"forges": [_FORGE, _OTHER], "credentials": [_CREDENTIAL, other], section: [entry]}
    before = _change_rows(hub)

    for dry_run in (True, False):
        refused = _apply(hub, document, dry_run=dry_run)

        assert refused.status_code == 422, refused.text
        (detail,) = refused.json()["detail"]
        assert (detail["loc"], detail["type"]) == (["body", section, 0, "credential"], "credential_forge_mismatch")
    assert _change_rows(hub) == before


def test_a_referrer_naming_an_unknown_credential_is_refused_at_its_credential(hub: HubHarness) -> None:
    refused = _apply(hub, {"forges": [_FORGE], "repositories": [{**_REPO, "credential": "nope"}]})

    assert refused.status_code == 422, refused.text
    (detail,) = refused.json()["detail"]
    assert (detail["loc"], detail["type"]) == (["body", "repositories", 0, "credential"], "credential_unavailable")


@pytest.mark.parametrize(
    ("section", "stray"), [("repositories", {"secret_name": "gh"}), ("work_sources", {"secret": "gh"})]
)
def test_a_version_3_referrer_with_an_old_secret_field_is_an_invalid_document(
    hub: HubHarness, section: str, stray: dict[str, str]
) -> None:
    entry = {**(_REPO if section == "repositories" else _SOURCE), **stray}
    before = _change_rows(hub)

    refused = _apply(hub, {"forges": [_FORGE], "credentials": [_CREDENTIAL], section: [entry]})

    assert refused.status_code == 422
    assert refused.json()["type"] == CONFIG_DOCUMENT_INVALID
    (detail,) = refused.json()["detail"]
    assert detail["loc"] == ["body", section, 0, next(iter(stray))]
    assert _change_rows(hub) == before


@pytest.mark.parametrize(("field", "value"), [("forge", "other"), ("binding", "github.app")])
def test_a_credential_entry_changing_its_forge_or_binding_is_refused(hub: HubHarness, field: str, value: str) -> None:
    assert _apply(hub, {"forges": [_FORGE, _OTHER], "credentials": [_CREDENTIAL]}).status_code == 200
    before = _change_rows(hub)

    refused = _apply(hub, {"credentials": [{**_CREDENTIAL, field: value}]})

    assert refused.status_code == 422, refused.text
    (detail,) = refused.json()["detail"]
    assert (detail["loc"], detail["type"]) == (["body", "credentials", 0, field], "credential_binding_immutable")
    assert _change_rows(hub) == before


def test_a_credential_entry_moving_its_secret_moves_every_record_acting_with_it(hub: HubHarness) -> None:
    assert _apply(hub, _document()).status_code == 200
    stated = _apply(hub, {"credentials": [{**_CREDENTIAL, "secret": "ops"}], "repositories": [_REPO]})

    assert stated.status_code == 200, stated.text
    assert [(o["kind"], o["op"]) for o in stated.json()["outcomes"]] == [
        ("credential", "edit"),
        ("repository", "edit"),
    ]
    assert hub.client.get("/api/repositories/code").json()["secret_name"] == "ops"
    assert hub.client.get("/api/work-sources/issues").json()["secret"] == "ops"
    assert _credential(hub)["authority"] == "secret/ops"
    exported = hub.client.get("/api/config/export", params={"version": 3}).json()
    again = _apply(hub, {k: v for k, v in exported.items() if k != "version"})
    assert {o["op"] for o in again.json()["outcomes"]} == {"unchanged"}


def test_a_credential_secret_move_reaches_a_declared_work_source_that_names_no_credential(hub: HubHarness) -> None:
    assert _apply(hub, _document()).status_code == 200
    source = {k: v for k, v in _SOURCE.items() if k != "credential"}

    stated = _apply(hub, {"credentials": [{**_CREDENTIAL, "secret": "ops"}], "work_sources": [source]})

    assert stated.status_code == 200, stated.text
    moved = hub.client.get("/api/work-sources/issues").json()
    assert (moved["secret"], moved["credential_id"]) == ("ops", _credential(hub)["credential_id"])
    again = _apply(hub, {"work_sources": [{**source, "annotate": True}]})
    assert again.status_code == 200, again.text


def test_a_credential_entry_narrowing_below_a_repository_s_policy_is_refused_in_a_dry_run_too(
    hub: HubHarness,
) -> None:
    assert _apply(hub, _document()).status_code == 200
    before = _change_rows(hub)
    narrowed = {**_CREDENTIAL, "settings": {"variant": "fine-grained"}}

    for dry_run in (True, False):
        refused = _apply(hub, {"credentials": [narrowed]}, dry_run=dry_run)

        assert refused.status_code == 422, refused.text
        (detail,) = refused.json()["detail"]
        assert (detail["loc"], detail["type"]) == (["body", "credentials", 0, "settings"], "forge_capability_missing")
    assert _change_rows(hub) == before


def test_a_credential_entry_taking_an_authority_another_credential_holds_is_refused_at_its_secret(
    hub: HubHarness,
) -> None:
    assert _apply(hub, {"forges": [_FORGE], "credentials": [_CREDENTIAL]}).status_code == 200

    refused = _apply(hub, {"credentials": [{**_CREDENTIAL, "slug": "twin", "name": "twin"}]})

    assert refused.status_code == 422, refused.text
    (detail,) = refused.json()["detail"]
    assert (detail["loc"], detail["type"]) == (["body", "credentials", 0, "secret"], "credential_authority_taken")


def test_a_version_3_export_declares_the_credential_a_referrer_s_old_field_would_resolve_to(hub: HubHarness) -> None:
    repository = {k: v for k, v in _REPO.items() if k != "credential"}
    carried = {"version": 2, "forges": [_FORGE], "repositories": [{**repository, "secret_name": "gh"}]}
    assert hub.client.post("/api/config/apply", json=carried).status_code == 200
    with hub.engine.begin() as conn:
        conn.execute(hub_scoped(sa.update(s.repositories).values(credential_id=None)))
        conn.execute(hub_scoped(sa.delete(s.credentials)))
    assert hub.client.get("/api/credentials").json()["credentials"] == []

    exported = hub.client.get("/api/config/export", params={"version": 3}).json()

    (declared,) = exported["credentials"]
    assert CredentialEntry.model_validate(declared).model_dump() == {
        "slug": "gh",
        "name": "gh",
        "forge": "enterprise",
        "binding": "github.token",
        "secret": "gh",
        "settings": {},
    }
    assert [r["credential"] for r in exported["repositories"]] == ["gh"]
    assert hub.client.get("/api/credentials").json()["credentials"] == []
    applied = _apply(hub, {k: v for k, v in exported.items() if k != "version"})
    assert applied.status_code == 200, applied.text
    assert hub.client.get("/api/repositories/code").json()["credential_id"] == _credential(hub, "gh")["credential_id"]


def test_a_version_3_export_declares_a_retired_credential_a_referrer_s_old_field_resolves_to(
    hub: HubHarness,
) -> None:
    repository = {k: v for k, v in _REPO.items() if k != "credential"}
    carried = {"version": 2, "forges": [_FORGE], "repositories": [{**repository, "secret_name": "gh"}]}
    assert hub.client.post("/api/config/apply", json=carried).status_code == 200
    with hub.engine.begin() as conn:
        conn.execute(hub_scoped(sa.update(s.repositories).values(credential_id=None)))
    assert hub.client.post("/api/credentials/gh/retire").status_code == 200

    exported = hub.client.get("/api/config/export", params={"version": 3}).json()

    assert [c["slug"] for c in exported["credentials"]] == ["gh"]
    assert [r["credential"] for r in exported["repositories"]] == ["gh"]
    applied = _apply(hub, {k: v for k, v in exported.items() if k != "version"})
    assert applied.status_code == 200, applied.text
    assert _credential(hub, "gh")["retired"] is False


def test_the_credentials_schema_is_served_at_version_3_only(hub: HubHarness) -> None:
    served = hub.client.get("/api/config/schema/credentials", params={"version": 3})
    assert served.status_code == 200
    assert served.json() == CredentialEntry.model_json_schema()
    assert "credential" in hub.client.get("/api/config/schema/repositories", params={"version": 3}).json()["required"]

    assert hub.client.get("/api/config/schema/credentials", params={"version": 2}).status_code == 404
    assert hub.client.get("/api/config/schema/credentials").status_code == 404
    for path in ("/api/config/schema/credentials", "/api/config/export"):
        refused = hub.client.get(path, params={"version": 4})
        assert refused.status_code == 422
        (detail,) = refused.json()["detail"]
        assert (detail["loc"], detail["type"]) == (["query", "version"], "config_version_retired")
