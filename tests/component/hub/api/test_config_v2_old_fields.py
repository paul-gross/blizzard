"""A version-2 document's old secret fields resolve to credentials in its apply (component tier, wired hub app): a
repository or work source naming a secret no credential acts for creates the ``github.token`` credential reading it in
the apply's one unit, under the apply's door and id; one already acting for it is named instead; a retired one is
refused at the entry's old field; and a dry run writes nothing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from blizzard.hub.domain.config.credentials import CredentialSlug
from tests.support.scaffolding import HubHarness, build_hub

_FORGE = {"slug": "enterprise", "kind": "github", "api_base": "https://api.enterprise.test"}
_REPO = {
    "slug": "code",
    "forge": "enterprise",
    "owner": "acme",
    "repo": "code",
    "base_branch": "main",
    "secret_name": "gh",
}
_SOURCE = {"slug": "issues", "forge": "enterprise", "provider": "github", "locator": "acme/code", "secret": "gh"}


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    harness = build_hub(tmp_path, repositories=(), link=False)
    for name in ("gh", "ops"):
        assert harness.client.post("/api/secrets", json={"name": name, "value": "v"}).status_code == 201
    return harness


def _apply(hub: HubHarness, sections: dict[str, Any], *, dry_run: bool = False) -> Any:
    return hub.client.post("/api/config/apply", params={"dry_run": dry_run}, json={"version": 2, **sections})


def _credentials(hub: HubHarness) -> list[dict[str, Any]]:
    return hub.client.get("/api/credentials", params={"include_retired": True}).json()["credentials"]


def _changes(hub: HubHarness, kind: str) -> list[dict[str, Any]]:
    return hub.client.get("/api/config/changes", params={"record_kind": kind}).json()["changes"]


def test_a_repository_and_a_work_source_naming_a_secret_no_credential_acts_for_create_one_and_name_it(
    hub: HubHarness,
) -> None:
    applied = _apply(hub, {"forges": [_FORGE], "repositories": [_REPO], "work_sources": [_SOURCE]})

    assert applied.status_code == 200, applied.text
    assert applied.json()["warnings"] == []
    assert "Deprecation" not in applied.headers
    (credential,) = _credentials(hub)
    assert (credential["slug"], credential["binding"], credential["secret_name"], credential["forge_slug"]) == (
        "gh",
        "github.token",
        "gh",
        "enterprise",
    )
    assert credential["authority"] == "secret/gh"
    repository = hub.client.get("/api/repositories/code").json()
    source = hub.client.get("/api/work-sources/issues").json()
    assert repository["credential_id"] == source["credential_id"] == credential["credential_id"]
    (row,) = _changes(hub, "credential")
    assert (row["door"], row["apply_id"], row["op"], row["record_key"]) == (
        "apply",
        applied.json()["apply_id"],
        "create",
        credential["credential_id"],
    )
    written = [c for c in hub.client.get("/api/config/changes").json()["changes"] if c["door"] == "apply"]
    assert [c["record_kind"] for c in written] == ["repository", "work_source", "credential", "forge"]


def test_an_old_field_naming_the_secret_a_credential_already_acts_for_names_that_credential(hub: HubHarness) -> None:
    assert _apply(hub, {"forges": [_FORGE]}).status_code == 200
    held = hub.client.post(
        "/api/credentials",
        json={"slug": "deploy", "forge": "enterprise", "binding": "github.token", "secret": "gh", "settings": {}},
    )
    assert held.status_code == 201, held.text

    applied = _apply(hub, {"repositories": [_REPO]})

    assert applied.status_code == 200, applied.text
    assert [c["slug"] for c in _credentials(hub)] == ["deploy"]
    assert hub.client.get("/api/repositories/code").json()["credential_id"] == held.json()["credential_id"]


def test_an_edit_moving_the_old_field_names_the_credential_of_the_new_secret(hub: HubHarness) -> None:
    assert _apply(hub, {"forges": [_FORGE], "repositories": [_REPO]}).status_code == 200

    moved = _apply(hub, {"repositories": [{**_REPO, "secret_name": "ops"}]})

    assert moved.status_code == 200, moved.text
    assert [o["op"] for o in moved.json()["outcomes"]] == ["edit"]
    by_slug = {c["slug"]: c for c in _credentials(hub)}
    assert set(by_slug) == {"gh", "ops"}
    repository = hub.client.get("/api/repositories/code").json()
    assert (repository["credential_id"], repository["secret_name"]) == (by_slug["ops"]["credential_id"], "ops")


def test_reapplying_the_document_settles_unchanged_and_creates_no_other_credential(hub: HubHarness) -> None:
    document = {"forges": [_FORGE], "repositories": [_REPO], "work_sources": [_SOURCE]}
    assert _apply(hub, document).status_code == 200
    before = hub.client.get("/api/config/changes").json()["total"]

    again = _apply(hub, document)

    assert again.status_code == 200, again.text
    assert {o["op"] for o in again.json()["outcomes"]} == {"unchanged"}
    assert hub.client.get("/api/config/changes").json()["total"] == before
    assert len(_credentials(hub)) == 1


@pytest.mark.parametrize(
    ("section", "entry", "field"),
    [("repositories", _REPO, "secret_name"), ("work_sources", _SOURCE, "secret")],
)
def test_a_retired_credential_at_the_old_field_s_authority_is_refused_at_the_old_field(
    hub: HubHarness, section: str, entry: dict[str, Any], field: str
) -> None:
    assert _apply(hub, {"forges": [_FORGE]}).status_code == 200
    held = hub.client.post(
        "/api/credentials",
        json={"slug": "deploy", "forge": "enterprise", "binding": "github.token", "secret": "gh", "settings": {}},
    )
    assert held.status_code == 201, held.text
    assert hub.client.post("/api/credentials/deploy/retire").status_code == 200
    before = hub.client.get("/api/config/changes").json()["total"]

    for dry_run in (True, False):
        refused = _apply(hub, {section: [entry]}, dry_run=dry_run)

        assert refused.status_code == 422, refused.text
        (detail,) = refused.json()["detail"]
        assert (detail["loc"], detail["type"]) == (["body", section, 0, field], "credential_unavailable")
    assert hub.client.get("/api/config/changes").json()["total"] == before
    assert hub.client.get(f"/api/{section.replace('_', '-')}/{entry['slug']}").status_code == 404


def test_a_dry_run_writes_nothing_and_answers_the_real_apply_s_outcomes(hub: HubHarness) -> None:
    document = {"forges": [_FORGE], "repositories": [_REPO], "work_sources": [_SOURCE]}
    before = hub.client.get("/api/config/changes").json()["total"]

    dry = _apply(hub, document, dry_run=True)

    assert dry.status_code == 200, dry.text
    assert hub.client.get("/api/config/changes").json()["total"] == before
    assert _credentials(hub) == []
    assert hub.services.credential_records.get_by_slug(CredentialSlug.parse("gh")) is None
    real = _apply(hub, document)
    assert real.status_code == 200, real.text

    def shape(outcomes: list[dict[str, Any]]) -> list[tuple[str, str, str, list[str]]]:
        return [(o["kind"], o["key"], o["op"], [d["field"] for d in o["diff"]]) for o in outcomes]

    assert shape(dry.json()["outcomes"]) == shape(real.json()["outcomes"])
    assert [d["field"] for d in real.json()["outcomes"][2]["diff"]][-1] == "credential"
