"""Which credential a configured record acts with: the one it names, an implicit token over its secret, or none
(unit tier)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.config.credential_bindings import CREDENTIAL_BINDINGS
from blizzard.hub.domain.config.credentials import (
    ConfiguredCredential,
    CredentialDraft,
    CredentialId,
    CredentialSlug,
    CredentialUnavailable,
    IReadCredentialRepository,
    ResolvedCredential,
    resolve_credential,
)
from blizzard.hub.domain.config.forges import ForgeId
from blizzard.hub.domain.config.repositories import ConfiguredRepository, RepositoryFields
from blizzard.hub.domain.config.work_sources import ConfiguredWorkSource, WorkSourceFields
from blizzard.hub.domain.kernel.transactions import Transaction

_AT = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
_CTX = ChangeContext(actor="ops", door=Door.CLI)
_FORGE = ForgeId("frg_github")
_ID = CredentialId("cred_deploy")


def _credential(*, retired: bool) -> ConfiguredCredential:
    draft = CredentialDraft(
        slug="deploy",
        name=None,
        forge_id=_FORGE,
        binding="github.token",
        secret_name="deploy-token",
        settings={"variant": "fine-grained"},
    )
    record, _ = ConfiguredCredential.new(draft, CREDENTIAL_BINDINGS["github.token"], _CTX, at=_AT, credential_id=_ID)
    return replace(record, revision=4, retired=retired)


class _Credentials:
    def __init__(self, *held: ConfiguredCredential) -> None:
        self._held = {record.credential_id: record for record in held}

    def get(self, credential_id: CredentialId) -> ConfiguredCredential | None:
        return self._held.get(credential_id)

    def get_many(
        self, credential_ids: list[CredentialId], *, transaction: Transaction | None = None
    ) -> dict[CredentialId, ConfiguredCredential]:
        return {key: self._held[key] for key in credential_ids if key in self._held}

    def list_all(self, *, include_retired: bool) -> list[ConfiguredCredential]:
        return [record for record in self._held.values() if include_retired or not record.retired]

    def get_by_slug(self, slug: CredentialSlug) -> ConfiguredCredential | None:
        return next((record for record in self._held.values() if record.slug == slug.value), None)

    def get_by_former_slug(self, slug: CredentialSlug) -> ConfiguredCredential | None:
        return None

    def resolve(self, value: str) -> ConfiguredCredential | None:
        return self._held.get(CredentialId(value))


_credentials_conforms: type[IReadCredentialRepository] = _Credentials


def _repository(*, secret_name: str = "gh", credential_id: CredentialId | None = None) -> ConfiguredRepository:
    fields = RepositoryFields(forge_id=_FORGE, owner="acme", repo="demo", base_branch="master", secret_name=secret_name)
    record, _ = ConfiguredRepository.new("demo", None, fields, _CTX, at=_AT)
    return replace(record, credential_id=credential_id)


def _work_source(*, secret: str | None, credential_id: CredentialId | None = None) -> ConfiguredWorkSource:
    fields = WorkSourceFields(provider="github", locator="acme/demo", forge_id=_FORGE, annotate=False, secret="gh")
    record, _ = ConfiguredWorkSource.new("demo", None, fields, _CTX, at=_AT)
    return replace(record, fields=replace(fields, secret=secret), credential_id=credential_id)


@pytest.mark.parametrize("retired", [False, True])
@pytest.mark.parametrize("referrer", ["repository", "work_source"])
def test_a_record_naming_a_credential_acts_with_it_retired_or_not(retired: bool, referrer: str) -> None:
    record = (
        _repository(credential_id=_ID) if referrer == "repository" else _work_source(secret="gh", credential_id=_ID)
    )

    resolved = resolve_credential(record, _Credentials(_credential(retired=retired)))

    assert resolved == ResolvedCredential(
        credential_id=_ID,
        forge_id=_FORGE,
        binding="github.token",
        secret_name="deploy-token",
        settings={"variant": "fine-grained"},
        revision=4,
        authority="secret/deploy-token",
    )


def test_a_record_naming_an_unknown_credential_is_unavailable() -> None:
    with pytest.raises(CredentialUnavailable) as caught:
        resolve_credential(_repository(credential_id=_ID), _Credentials())
    assert not caught.value.retired


@pytest.mark.parametrize(
    "record", [_repository(secret_name="gh"), _work_source(secret="gh")], ids=["repository", "work_source"]
)
def test_a_record_naming_only_a_secret_acts_with_an_implicit_token_of_no_variant(
    record: ConfiguredRepository | ConfiguredWorkSource,
) -> None:
    assert resolve_credential(record, _Credentials(_credential(retired=False))) == ResolvedCredential(
        credential_id=None,
        forge_id=_FORGE,
        binding="github.token",
        secret_name="gh",
        settings={},
        revision=None,
        authority="secret/gh",
    )


def test_a_work_source_naming_neither_acts_with_nothing() -> None:
    assert resolve_credential(_work_source(secret=None), _Credentials()) is None
