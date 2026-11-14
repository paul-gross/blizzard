"""Repositories and work sources acting with credentials over the real stores: an old secret field resolving to its
``github.token`` credential — created once, then reused, refused when retired, and created once by two resolutions
racing for it — a credential's secret edit mirrored into every record acting with it, and a credential edit refused
for the landing policy of a repository acting with it; the hub invariants holding after each write (component tier,
migrated sqlite-on-disk)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.landing_policy import LandingPolicy
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, Door, FieldChange, RecordKind, RecordRef
from blizzard.hub.domain.config.credential_naming import CredentialConflict, CredentialUnavailable
from blizzard.hub.domain.config.credential_resolution import CredentialCapabilityMissing, CredentialChoice
from blizzard.hub.domain.config.credentials import (
    ConfiguredCredential,
    CredentialDraft,
    CredentialEdit,
)
from blizzard.hub.domain.config.forge_resolution import ForgeChoice
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeFields
from blizzard.hub.domain.config.landing_policies import ForgeCapabilityMissing
from blizzard.hub.domain.config.repositories import ConfiguredRepository, RepositoryDraft, RepositoryEdit
from blizzard.hub.domain.config.secrets import SecretName
from blizzard.hub.domain.config.work_sources import WorkSourceDraft
from blizzard.hub.secrets import hub_key_provider
from blizzard.hub.store.internal import credential_record_store
from blizzard.hub.store.internal.config_change_store import ConfigChangeStore
from blizzard.hub.store.internal.credential_record_store import CredentialRecordStore
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore
from blizzard.hub.store.internal.work_source_record_store import WorkSourceRecordStore
from blizzard.tools.invariants import hub_invariants
from tests.support.scaffolding import OP, config_authoring, hub_store_connections, migrated_hub_at

_NOW = datetime(2026, 11, 13, 12, 0, 0, tzinfo=UTC)
_API = ChangeContext(actor="alice", door=Door.API)
_GHE = "https://ghe.acme.internal/api/v3"


def _repo(
    repo: str, *, secret: str | None = "gh", policy: LandingPolicy = LandingPolicy.MERGE_ON_CHECKS
) -> RepositoryDraft:
    return RepositoryDraft(owner="acme", repo=repo, base_branch="main", secret_name=secret, landing_policy=policy)


class _World:
    def __init__(self, tmp_path: Path) -> None:
        db_url = f"sqlite:///{tmp_path / 'hub.db'}"
        migrated_hub_at(db_url)
        self.engine: Engine = create_engine_from_url(db_url)
        connections = hub_store_connections(self.engine)
        self.credentials = CredentialRecordStore(connections)
        self.repositories = RepositoryRecordStore(connections)
        self.sources = WorkSourceRecordStore(connections)
        self.log = ConfigChangeStore(connections)
        keys = hub_key_provider({}, data_dir=tmp_path / "data")
        self.authoring: ConfigAuthoring = config_authoring(self.engine, keys=keys, clock=FixedClock(_NOW))
        for name in ("gh", "gh2"):
            self.authoring.create_secret(SecretName.parse(name), f"tok-{name}", OP)
        self.forge: ConfiguredForge = self.authoring.create_forge(
            "ghe", "Acme GHE", "github", ForgeFields(_GHE, None), _API
        )
        self.on_forge = ForgeChoice(forge="ghe", named=self.forge)

    def repository(self, repo: str = "demo", **kwargs: Any) -> ConfiguredRepository:
        credential: CredentialChoice = kwargs.pop("credential", CredentialChoice())
        return self.authoring.create_repository_naming(
            repo, None, _repo(repo, **kwargs), self.on_forge, _API, credential=credential
        ).record

    def credential(self, *, slug: str = "deploy", secret: str = "gh", **settings: object) -> ConfiguredCredential:
        draft = CredentialDraft(slug, None, self.forge.forge_id, "github.token", secret, settings)
        return self.authoring.create_credential(draft, _API)

    def changes(self, kind: RecordKind, key: str) -> list[tuple[ChangeOp, tuple[FieldChange, ...]]]:
        rows = self.log.page(before=None, limit=20, record_kind=kind, record_key=key)
        return [(row.op, row.diff) for row in reversed(rows)]

    def violations(self) -> list[str]:
        return [f"{v.invariant}: {v.detail}" for v in hub_invariants(self.engine).run()]


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def test_an_old_field_alone_creates_one_github_token_credential_then_reuses_it(world: _World) -> None:
    first = world.authoring.create_repository_naming("demo", None, _repo("demo"), world.on_forge, _API)

    (credential,) = world.credentials.list_all(include_retired=True)
    assert (credential.slug, credential.name, credential.fields.binding) == ("gh", "gh", "github.token")
    assert (credential.authority, credential.fields.settings, credential.created_by) == ("secret/gh", {}, "alice")
    assert first.record.fields.credential_id == credential.credential_id
    assert [(use.field, use.credential) for use in first.old_fields] == [("secret_name", credential)]
    assert world.repositories.get(first.record.repository_id) == first.record
    assert world.changes(RecordKind.CREDENTIAL, credential.credential_id)[0][0] is ChangeOp.CREATE
    assert (
        FieldChange("credential", None, credential.credential_id)
        in world.changes(RecordKind.REPOSITORY, first.record.repository_id)[0][1]
    )

    second = world.authoring.create_repository_naming("other", None, _repo("other"), world.on_forge, _API)
    source = world.authoring.create_work_source_naming(
        "acme", None, WorkSourceDraft("github", "acme/demo", annotate=False, secret="gh"), world.on_forge, _API
    )

    assert world.credentials.list_all(include_retired=True) == [credential]
    assert second.record.fields.credential_id == source.record.fields.credential_id == credential.credential_id
    assert [use.credential for use in (*second.old_fields, *source.old_fields)] == [credential, credential]
    assert world.violations() == []


def test_an_old_field_equal_to_the_record_s_resolves_nothing_and_a_new_one_resolves_its_own(world: _World) -> None:
    record = world.repository()
    unchanged = world.authoring.edit_repository_naming(
        record, RepositoryEdit(secret_name="gh", base_branch="trunk"), ForgeChoice(), _API
    )
    assert (unchanged.old_fields, unchanged.record.fields.credential_id) == ((), record.fields.credential_id)

    record = unchanged.record
    moved = world.authoring.edit_repository_naming(record, RepositoryEdit(secret_name="gh2"), ForgeChoice(), _API)

    by_slug = {credential.slug: credential for credential in world.credentials.list_all(include_retired=True)}
    assert sorted(by_slug) == ["gh", "gh2"]
    assert moved.record.fields.credential_id == by_slug["gh2"].credential_id
    assert moved.record.fields.secret_name == "gh2" and moved.record.revision == record.revision + 1
    assert world.violations() == []


def test_a_credential_named_by_slug_is_used_and_its_secret_mirrored(world: _World) -> None:
    deploy = world.credential(secret="gh2")
    named = CredentialChoice(credential="deploy", named=deploy)

    record = world.repository(secret=None, credential=named)

    assert (record.fields.credential_id, record.fields.secret_name) == (deploy.credential_id, "gh2")
    assert world.credentials.list_all(include_retired=True) == [deploy]
    assert world.violations() == []


def test_an_old_field_naming_a_retired_credential_is_refused_unavailable(world: _World) -> None:
    world.authoring.retire_credential(world.credential(), _API)

    with pytest.raises(CredentialUnavailable, match="credential deploy is retired") as caught:
        world.repository()

    assert caught.value.code == "credential_unavailable"
    assert world.repositories.list_all(include_retired=True) == []
    assert len(world.credentials.list_all(include_retired=True)) == 1


def test_a_linked_repository_s_secret_moved_without_naming_a_credential_is_refused(world: _World) -> None:
    record = world.repository()

    with pytest.raises(CredentialConflict):
        world.authoring.edit_repository(record, RepositoryEdit(secret_name="gh2"), _API)

    assert world.repositories.get(record.repository_id) == record


def test_two_resolutions_of_one_new_authority_leave_one_credential_both_naming_it(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second resolution misses the first's credential until its own insert loses to it at
    ``UNIQUE (tenant_id, forge_id, authority)`` — in a savepoint, so its unit reads the winner on and writes again
    naming it."""
    winner = world.repository("first")
    store = world.authoring._credentials  # pyright: ignore[reportPrivateUsage]
    blind = {"reads": 2, "holders": 1, "lost": 0}
    real_read, real_holder = store.get_by_authority, credential_record_store._authority_holder  # pyright: ignore[reportPrivateUsage]
    real_lost = credential_record_store.lost_race

    def unseen_read(*args: Any, **kwargs: Any) -> ConfiguredCredential | None:
        if blind["reads"]:
            blind["reads"] -= 1
            return None
        return real_read(*args, **kwargs)

    def unseen_holder(*args: Any) -> str | None:
        if blind["holders"]:
            blind["holders"] -= 1
            return None
        return real_holder(*args)

    def counted_loss(*args: Any) -> Exception:
        blind["lost"] += 1
        return real_lost(*args)

    monkeypatch.setattr(store, "get_by_authority", unseen_read)
    monkeypatch.setattr(credential_record_store, "_authority_holder", unseen_holder)
    monkeypatch.setattr(credential_record_store, "lost_race", counted_loss)

    loser = world.authoring.create_repository_naming("second", None, _repo("second"), world.on_forge, _API)

    (credential,) = world.credentials.list_all(include_retired=True)
    assert blind["lost"] == 1
    assert loser.record.fields.credential_id == winner.fields.credential_id == credential.credential_id
    assert [use.credential for use in loser.old_fields] == [credential]
    assert world.violations() == []


def test_a_credential_s_secret_edit_rewrites_every_referrer_s_mirror_with_its_own_change_row(world: _World) -> None:
    active = world.repository()
    retired = world.authoring.retire_repository(world.repository("old"), _API)
    source = world.authoring.create_work_source_naming(
        "acme", None, WorkSourceDraft("github", "acme/demo", annotate=False, secret="gh"), world.on_forge, _API
    ).record
    (credential,) = world.credentials.list_all(include_retired=True)

    edited = world.authoring.edit_credential(credential, CredentialEdit(secret_name="gh2"), _API)

    assert edited.authority == "secret/gh2"
    for before in (active, retired):
        after = world.repositories.get(before.repository_id)
        assert after is not None
        assert (after.fields.secret_name, after.revision, after.retired) == ("gh2", before.revision + 1, before.retired)
        assert world.changes(RecordKind.REPOSITORY, before.repository_id)[-1] == (
            ChangeOp.EDIT,
            (FieldChange("secret_name", "gh", "gh2"),),
        )
    mirrored = world.sources.get(source.work_source_id)
    assert mirrored is not None and (mirrored.fields.secret, mirrored.revision) == ("gh2", source.revision + 1)
    assert world.changes(RecordKind.WORK_SOURCE, source.work_source_id)[-1] == (
        ChangeOp.EDIT,
        (FieldChange("secret", "gh", "gh2"),),
    )
    assert world.violations() == []


def test_a_fine_grained_edit_behind_a_merge_on_checks_repository_is_refused_listing_it(world: _World) -> None:
    gated = world.repository()
    world.repository("parked", policy=LandingPolicy.PARK)
    (credential,) = world.credentials.list_all(include_retired=True)

    with pytest.raises(CredentialCapabilityMissing) as caught:
        world.authoring.edit_credential(credential, CredentialEdit(settings={"variant": "fine-grained"}), _API)

    assert caught.value.code == "forge_capability_missing"
    assert caught.value.referrers == [RecordRef(RecordKind.REPOSITORY, gated.repository_id, "demo")]
    assert world.credentials.get(credential.credential_id) == credential
    classic = world.authoring.edit_credential(credential, CredentialEdit(settings={"variant": "classic"}), _API)
    assert classic.fields.settings == {"variant": "classic"}
    assert world.violations() == []


def test_a_fine_grained_credential_behind_merge_on_checks_is_refused_at_repository_create(world: _World) -> None:
    fine = world.credential(variant="fine-grained")

    with pytest.raises(ForgeCapabilityMissing, match="through credential deploy") as caught:
        world.repository(secret=None, credential=CredentialChoice(credential="deploy", named=fine))

    assert caught.value.credential == "deploy"
    assert world.repositories.list_all(include_retired=True) == []
    parked = world.repository(secret=None, policy=LandingPolicy.PARK, credential=CredentialChoice("deploy", fine))
    assert parked.fields.credential_id == fine.credential_id
    assert world.violations() == []
