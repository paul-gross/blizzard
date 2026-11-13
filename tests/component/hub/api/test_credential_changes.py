"""The config change log over credentials: a credential's rows read labelled by its current slug, and a filter by
slug narrows to them."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.config.credentials import CredentialDraft, CredentialEdit
from blizzard.hub.domain.config.forges import ForgeFields
from tests.support.scaffolding import FIXTURE_FORGE_SECRET, HubHarness, build_hub, ensure_fixture_secret

_CLI = ChangeContext(actor="alice", door=Door.CLI)


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    harness = build_hub(tmp_path, repositories=(), link=False)
    ensure_fixture_secret(harness.client)
    return harness


def test_a_credentials_changes_read_labelled_by_its_current_slug_and_filter_by_it(hub: HubHarness) -> None:
    authoring = hub.services.config_authoring
    forge = authoring.create_forge("ghe", "Acme GHE", "github", ForgeFields("https://ghe.acme/api/v3", None), _CLI)
    draft = CredentialDraft(
        slug="deploy",
        name=None,
        forge_id=forge.forge_id,
        binding="github.token",
        secret_name=FIXTURE_FORGE_SECRET,
        settings={},
    )
    credential = authoring.create_credential(draft, _CLI)
    authoring.edit_credential(credential, CredentialEdit(slug="renamed"), _CLI)

    every = hub.client.get("/api/config/changes", params={"record_kind": "credential"}).json()
    assert [(c["record_key"], c["record_label"], c["op"]) for c in every["changes"]] == [
        (credential.credential_id, "renamed", "edit"),
        (credential.credential_id, "renamed", "create"),
    ]
    by_slug = hub.client.get(
        "/api/config/changes", params={"record_kind": "credential", "record_key": "renamed"}
    ).json()
    assert by_slug["total"] == 2
    assert [c["record_key"] for c in by_slug["changes"]] == [credential.credential_id] * 2
    unknown = hub.client.get(
        "/api/config/changes", params={"record_kind": "credential", "record_key": "missing"}
    ).json()
    assert (unknown["total"], unknown["changes"]) == (0, [])
