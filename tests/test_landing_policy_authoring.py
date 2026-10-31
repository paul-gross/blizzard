"""``ConfigAuthoring`` over the real stores, checking a repository's landing policy against what its forge offers, and
clearing its park timeout (component tier, migrated sqlite-on-disk)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, func

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.config.apply import (
    ApplyEntryRefused,
    ConfigDeclaration,
    RepositoryDeclaration,
    StoredConfig,
)
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.changes import (
    ChangeContext,
    ChangeOp,
    ConfigChange,
    Door,
    FieldChange,
    RecordKind,
    RecordState,
)
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forge_resolution import ForgeChoice
from blizzard.hub.domain.config.forges import GITHUB_API_BASE, ConfiguredForge, ForgeFields, ForgeId
from blizzard.hub.domain.config.landing_policies import ForgeCapabilityMissing, LandingPolicy
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    RepositoryDraft,
    RepositoryEdit,
    RepositoryFields,
    RepositoryId,
)
from blizzard.hub.domain.config.secrets import SecretName
from blizzard.hub.secrets import hub_key_provider
from blizzard.hub.store.internal.forge_record_store import ForgeRecordStore
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore
from blizzard.hub.store.schema import config_changes, repositories
from tests.support import OP, config_authoring, hub_store_connections, migrated_hub_at

pytestmark = pytest.mark.component

_NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
#: Offers every capability.
_FULL = ForgeId("frg_01KX9LANDINGFULL0000000000")
#: Offers every capability but a merge commit and reading checks.
_LACKING = ForgeId("frg_01KX9LANDINGLACKING0000000")
_MISSING = (Capability.CHANGES_MERGE_MERGE, Capability.CHECKS_READ)
_REPO = RepositoryFields(forge_id=_FULL, owner="acme", repo="demo", base_branch="master", secret_name="gh")
_CLI = ChangeContext(actor="alice", door=Door.CLI)
_APPLY = ChangeContext(actor="alice", door=Door.APPLY, apply_id="apl_ONE")


class _Offers:
    """What each forge offers, by its id — the test's own table."""

    def __init__(self, table: dict[str, frozenset[Capability]]) -> None:
        self.table = table
        self.asked: list[tuple[str, str | None]] = []

    def offered(self, forge: ConfiguredForge, repository: str | None = None) -> frozenset[Capability]:
        self.asked.append((forge.forge_id, repository))
        return self.table[forge.forge_id]


class _World:
    def __init__(self, tmp_path: Path) -> None:
        db_url = f"sqlite:///{tmp_path / 'hub.db'}"
        migrated_hub_at(db_url)
        self.engine: Engine = create_engine_from_url(db_url)
        connections = hub_store_connections(self.engine)
        self.repos = RepositoryRecordStore(connections)
        self.offers = _Offers({_FULL: frozenset(Capability), _LACKING: frozenset(Capability) - set(_MISSING)})
        keys = hub_key_provider({}, data_dir=tmp_path / "data")
        self.authoring: ConfigAuthoring = config_authoring(
            self.engine, keys=keys, clock=FixedClock(_NOW), offers=self.offers
        )
        forges = ForgeRecordStore(connections)
        self.forges: dict[ForgeId, ConfiguredForge] = {}
        for forge_id, slug, api_base in (
            (_FULL, "github", GITHUB_API_BASE),
            (_LACKING, "lacking", "https://git.example.com/api/v3"),
        ):
            forge, change = ConfiguredForge.new(
                slug, None, "github", ForgeFields(api_base, None), OP, at=_NOW, forge_id=forge_id
            )
            self.forges[forge_id] = forges.create(forge, change=change)
        self.authoring.create_secret(SecretName.parse("gh"), "tok-planted", OP)

    def repository_changes(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(
                hub_store_connections(self.engine)
                .select(config_changes, func.count())
                .where(config_changes.c.record_kind == RecordKind.REPOSITORY.value)
            ).scalar_one()

    def repository_rows(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(hub_store_connections(self.engine).select(repositories, func.count())).scalar_one()

    def create(self, forge: ForgeId = _FULL) -> ConfiguredRepository:
        return self.authoring.create_repository(
            "demo", None, RepositoryFields(forge, "acme", "demo", "master", "gh"), _CLI
        )


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def _assert_names_what_is_missing(refusal: ConfigFieldError) -> None:
    assert isinstance(refusal, ForgeCapabilityMissing)
    assert (refusal.code, refusal.field, refusal.missing) == ("forge_capability_missing", "landing_policy", _MISSING)
    for named in ("changes.merge.merge", "checks.read", "merge-on-checks", "lacking"):
        assert named in refusal.message


def test_a_create_on_a_forge_that_offers_the_policy_is_written_landing_by_it(world: _World) -> None:
    record = world.create()
    stored = world.repos.get(record.repository_id)
    assert stored is not None
    assert (stored.fields.landing_policy, stored.fields.park_timeout_hours) == (LandingPolicy.MERGE_ON_CHECKS, None)
    assert world.offers.asked == [(_FULL, None)]


def test_a_create_on_a_forge_lacking_the_policys_requirements_is_refused_writing_nothing(world: _World) -> None:
    with pytest.raises(ForgeCapabilityMissing) as refused:
        world.create(_LACKING)
    _assert_names_what_is_missing(refused.value)
    assert (world.repository_rows(), world.repository_changes()) == (0, 0)


def test_a_door_create_naming_a_lacking_forge_is_refused(world: _World) -> None:
    draft = RepositoryDraft(owner="acme", repo="demo", base_branch="master", secret_name="gh")
    with pytest.raises(ForgeCapabilityMissing) as refused:
        world.authoring.create_repository_naming(
            "demo", None, draft, ForgeChoice(forge="lacking", named=world.forges[_LACKING]), _CLI
        )
    _assert_names_what_is_missing(refused.value)
    assert world.repository_rows() == 0


def test_a_forge_change_onto_a_lacking_forge_is_refused_writing_nothing(world: _World) -> None:
    record = world.create()
    with pytest.raises(ForgeCapabilityMissing) as refused:
        world.authoring.edit_repository(record, RepositoryEdit(forge_id=_LACKING), _CLI)
    _assert_names_what_is_missing(refused.value)
    assert world.repos.get(record.repository_id) == record
    assert world.repository_changes() == 1


def test_a_policy_edit_is_checked_against_what_the_repositorys_forge_offers_it(world: _World) -> None:
    record = world.create()
    # The forge stopped offering what the policy needs since the repository was created.
    world.offers.table[_FULL] = world.offers.table[_LACKING]
    world.offers.asked.clear()
    edit = RepositoryEdit(landing_policy=LandingPolicy.MERGE_ON_CHECKS, base_branch="main")
    with pytest.raises(ForgeCapabilityMissing) as refused:
        world.authoring.edit_repository(record, edit, _CLI)
    assert refused.value.missing == _MISSING
    assert world.offers.asked == [(_FULL, record.repository_id)]
    assert world.repos.get(record.repository_id) == record


def test_an_edit_leaving_the_policy_and_forge_asks_nothing(world: _World) -> None:
    record = world.create()
    world.offers.asked.clear()
    edited = world.authoring.edit_repository(record, RepositoryEdit(base_branch="main"), _CLI)
    assert edited.fields.base_branch == "main"
    assert world.offers.asked == []


def _declaration(forge: ForgeId, **stated: object) -> ConfigDeclaration:
    return ConfigDeclaration(
        secrets=("gh",),
        repositories=(
            RepositoryDeclaration(
                slug="demo",
                name=None,
                fields=RepositoryFields(forge, "acme", "demo", "master", "gh"),
                edit=RepositoryEdit(**stated),  # type: ignore[arg-type]
                repository_id=RepositoryId("repo_demo"),
            ),
        ),
    )


def _apply(world: _World, declaration: ConfigDeclaration) -> None:
    stored = StoredConfig(
        work_sources={},
        repositories={r.slug: r for r in world.repos.list_all(include_retired=True)},
        secrets={"gh": RecordState.ACTIVE},
    )
    world.authoring.apply(declaration, stored, _APPLY, dry_run=False)


def test_a_document_creating_a_repository_on_a_lacking_forge_is_refused_at_its_entry(world: _World) -> None:
    with pytest.raises(ApplyEntryRefused) as refused:
        _apply(world, _declaration(_LACKING))
    assert (refused.value.section, refused.value.index) == ("repositories", 0)
    assert isinstance(refused.value.cause, ConfigFieldError)
    _assert_names_what_is_missing(refused.value.cause)
    assert world.repository_rows() == 0


def test_a_document_moving_a_repository_onto_a_lacking_forge_is_refused_at_its_entry(world: _World) -> None:
    _apply(world, _declaration(_FULL))
    with pytest.raises(ApplyEntryRefused) as refused:
        _apply(world, _declaration(_FULL, forge_id=_LACKING))
    assert isinstance(refused.value.cause, ConfigFieldError)
    _assert_names_what_is_missing(refused.value.cause)
    (stored,) = world.repos.list_all(include_retired=True)
    assert stored.fields.forge_id == _FULL


# --- Clearing ------------------------------------------------------------------------------


def test_an_edit_with_none_clears_the_park_timeout(world: _World) -> None:
    record = world.create()
    # A timeout only a parking policy may set, written straight to the store as such a policy would leave it.
    timed = replace(record, fields=replace(record.fields, park_timeout_hours=24), revision=record.revision + 1)
    change = ConfigChange.of(
        _CLI,
        RecordKind.REPOSITORY,
        record.repository_id,
        timed.revision,
        ChangeOp.EDIT,
        (FieldChange("park_timeout_hours", None, 24),),
        _NOW,
    )
    world.repos.update(timed, from_revision=record.revision, left_slug=None, change=change)
    held = world.repos.get(record.repository_id)
    assert held is not None and held.fields.park_timeout_hours == 24
    cleared = world.authoring.edit_repository(held, RepositoryEdit(park_timeout_hours=None), _CLI)
    assert cleared.fields.park_timeout_hours is None
    assert cleared.revision == held.revision + 1


@pytest.mark.parametrize(
    "field", ["slug", "name", "forge_id", "owner", "repo", "base_branch", "secret_name", "landing_policy"]
)
def test_an_edit_with_none_on_any_other_field_is_refused(world: _World, field: str) -> None:
    record = world.create()
    with pytest.raises(ConfigFieldError, match="must not be null"):
        world.authoring.edit_repository(record, RepositoryEdit(**{field: None}), _CLI)  # type: ignore[arg-type]
    assert world.repository_changes() == 1
