"""Credential bindings and the credential record by value: settings validation, authority, declared offers, and the
new, edit and set-retired verbs (unit tier)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, Door, FieldChange, RecordKind
from blizzard.hub.domain.config.credential_bindings import (
    CREDENTIAL_BINDINGS,
    FINE_GRAINED_WITHHELD,
    CredentialSettingsInvalid,
)
from blizzard.hub.domain.config.credentials import (
    ConfiguredCredential,
    CredentialBindingImmutable,
    CredentialBindingUnknown,
    CredentialDraft,
    CredentialEdit,
    CredentialId,
    binding_named,
)
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.config.forges import ForgeId

_AT = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
_CTX = ChangeContext(actor="ops", door=Door.CLI)
_TOKEN = CREDENTIAL_BINDINGS["github.token"]
_DRAFT = CredentialDraft(
    slug="deploy",
    name=None,
    forge_id=ForgeId("frg_github"),
    binding="github.token",
    secret_name="gh",
    settings={"variant": "classic"},
)
_ID = CredentialId("cred_deploy")


def _stored(*, retired: bool = False) -> ConfiguredCredential:
    record, _ = ConfiguredCredential.new(_DRAFT, _TOKEN, _CTX, at=_AT, credential_id=_ID)
    return replace(record, revision=3, retired=retired)


# --- The github.token binding ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("settings", "canonical"),
    [
        ({}, {}),
        ({"variant": "classic"}, {"variant": "classic"}),
        ({"variant": "fine-grained"}, {"variant": "fine-grained"}),
    ],
)
def test_a_github_token_takes_a_variant_or_none(settings: dict[str, object], canonical: dict[str, object]) -> None:
    assert _TOKEN.validate(settings) == canonical


@pytest.mark.parametrize(
    ("settings", "field"),
    [
        ({"variant": "app"}, "settings.variant"),
        ({"variant": None}, "settings.variant"),
        ({"scope": "x"}, "settings.scope"),
    ],
)
def test_a_github_token_refuses_any_other_setting_naming_it(settings: dict[str, object], field: str) -> None:
    with pytest.raises(CredentialSettingsInvalid) as caught:
        _TOKEN.validate(settings)
    assert caught.value.field == field
    assert caught.value.code == "credential_settings_invalid"


def test_a_github_token_acts_as_its_secret() -> None:
    assert _TOKEN.authority("gh", {"variant": "fine-grained"}) == "secret/gh"
    assert (_TOKEN.name, _TOKEN.forge_kind, _TOKEN.supplied) == ("github.token", "github", True)


def test_a_fine_grained_token_declares_its_kind_less_what_it_is_withheld() -> None:
    kind = frozenset(Capability)
    assert _TOKEN.declared_offers({"variant": "fine-grained"}, kind) == kind - FINE_GRAINED_WITHHELD
    assert Capability.CHECKS_READ not in _TOKEN.declared_offers({"variant": "fine-grained"}, kind)
    assert _TOKEN.declared_offers({"variant": "classic"}, kind) == kind
    assert _TOKEN.declared_offers({}, kind) == kind


def test_an_unknown_binding_is_refused_naming_it() -> None:
    with pytest.raises(CredentialBindingUnknown, match=r"'github\.app'"):
        binding_named("github.app")
    assert binding_named("github.token") is _TOKEN


# --- The record ------------------------------------------------------------------------------------------------------


def test_a_new_credential_derives_its_authority_and_logs_every_field_by_id() -> None:
    record, change = ConfiguredCredential.new(_DRAFT, _TOKEN, _CTX, at=_AT)
    assert record.credential_id.startswith("cred_")
    assert (record.slug, record.name, record.authority, record.revision) == ("deploy", "deploy", "secret/gh", 1)
    assert (change.record_kind, change.record_key, change.op, change.revision) == (
        RecordKind.CREDENTIAL,
        record.credential_id,
        ChangeOp.CREATE,
        1,
    )
    assert change.project_id is None
    assert change.diff == (
        FieldChange("slug", None, "deploy"),
        FieldChange("name", None, "deploy"),
        FieldChange("forge", None, "frg_github"),
        FieldChange("binding", None, "github.token"),
        FieldChange("secret", None, "gh"),
        FieldChange("settings", None, {"variant": "classic"}),
    )


def test_a_new_credential_refuses_its_binding_s_settings_and_a_bad_slug() -> None:
    with pytest.raises(CredentialSettingsInvalid):
        ConfiguredCredential.new(replace(_DRAFT, settings={"variant": "x"}), _TOKEN, _CTX, at=_AT)
    with pytest.raises(ConfigFieldError, match="slug"):
        ConfiguredCredential.new(replace(_DRAFT, slug="Not A Slug"), _TOKEN, _CTX, at=_AT)


def test_an_edit_of_the_secret_derives_the_authority_again() -> None:
    decided = _stored().edit(CredentialEdit(secret_name="gh2"), _TOKEN, _CTX, if_match=3, at=_AT)
    assert decided is not None
    edited, change = decided
    assert (edited.fields.secret_name, edited.authority, edited.revision) == ("gh2", "secret/gh2", 4)
    assert (change.op, change.diff) == (ChangeOp.EDIT, (FieldChange("secret", "gh", "gh2"),))


def test_an_edit_that_changes_nothing_writes_nothing() -> None:
    record = _stored()
    unchanged = CredentialEdit(slug="deploy", secret_name="gh", settings={"variant": "classic"})
    assert record.edit(unchanged, _TOKEN, _CTX, if_match=None, at=_AT) is None


@pytest.mark.parametrize(
    ("edit", "field"),
    [(CredentialEdit(forge_id=ForgeId("frg_other")), "forge"), (CredentialEdit(binding="github.token"), "binding")],
)
def test_an_edit_carrying_a_forge_or_binding_is_refused(edit: CredentialEdit, field: str) -> None:
    with pytest.raises(CredentialBindingImmutable) as caught:
        _stored().edit(edit, _TOKEN, _CTX, if_match=None, at=_AT)
    assert (caught.value.field, caught.value.code) == (field, "credential_binding_immutable")


def test_an_edit_checks_the_revision_first_and_refuses_null() -> None:
    with pytest.raises(ConfigRevisionConflict):
        _stored().edit(CredentialEdit(), _TOKEN, _CTX, if_match=2, at=_AT)
    with pytest.raises(ConfigFieldError, match="secret"):
        _stored().edit(CredentialEdit(secret_name=None), _TOKEN, _CTX, if_match=None, at=_AT)


def test_an_edit_of_the_settings_validates_them() -> None:
    with pytest.raises(CredentialSettingsInvalid, match=r"settings\.variant"):
        _stored().edit(CredentialEdit(settings={"variant": "x"}), _TOKEN, _CTX, if_match=None, at=_AT)
    decided = _stored().edit(CredentialEdit(settings={}), _TOKEN, _CTX, if_match=None, at=_AT)
    assert decided is not None
    assert decided[1].diff == (FieldChange("settings", {"variant": "classic"}, {}),)


def test_retire_and_enable_flip_once_and_a_redundant_verb_writes_nothing() -> None:
    record = _stored()
    assert record.set_retired(False, _CTX, if_match=None, at=_AT) is None
    decided = record.set_retired(True, _CTX, if_match=3, at=_AT)
    assert decided is not None
    retired, change = decided
    assert (retired.retired, retired.revision, change.op) == (True, 4, ChangeOp.RETIRE)
    assert change.diff == (FieldChange("retired", False, True),)
    assert retired.set_retired(True, _CTX, if_match=None, at=_AT) is None
    enabled = retired.set_retired(False, _CTX, if_match=None, at=_AT)
    assert enabled is not None and enabled[1].op is ChangeOp.ENABLE
    with pytest.raises(ConfigRevisionConflict):
        record.set_retired(True, _CTX, if_match=1, at=_AT)
