"""The carry-over planner's pure decisions — secret naming, repository derivation, skips and refusals (unit tier)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.hub.domain.config.carry_over import (
    MIGRATION_CONTEXT,
    CommitCoordinate,
    ExistingConfig,
    ImportOutcome,
    ImportPlan,
    LegacyImportRefused,
    LegacyStart,
    LegacyStartRefused,
    plan_import,
    secret_name_of,
)
from blizzard.hub.domain.config.changes import Door, RecordKind
from blizzard.hub.domain.config.legacy_keys import LegacyKeys, WorkSourceConfig
from blizzard.hub.domain.config.repositories import ConfiguredRepository, RepositoryFields
from blizzard.hub.domain.config.work_sources import ConfiguredWorkSource, WorkSourceFields

pytestmark = pytest.mark.unit

_AT = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
_NONE = ExistingConfig(secrets=frozenset(), work_sources=(), repositories=())
_FORGE = {"BZ_FORGE_URL": "https://api.github.com", "BZ_FORGE_TOKEN": "forge-tok"}


def _source(name: str, repo: str, token_env: str = "BZ_SHARED_TOKEN") -> WorkSourceConfig:
    return WorkSourceConfig(name=name, provider="github", repo=repo, token_env=token_env)


def _legacy(*sources: WorkSourceConfig, variables: tuple[str, ...] = ()) -> LegacyKeys:
    return LegacyKeys(config_path=Path("/hub/blizzard-hub.toml"), sources=sources, variables=variables)


def _plan(
    legacy: LegacyKeys,
    values: dict[str, str],
    commits: tuple[CommitCoordinate, ...] = (),
    existing: ExistingConfig = _NONE,
) -> ImportPlan:
    return plan_import(legacy, values, commits, existing, ctx=MIGRATION_CONTEXT, at=_AT)


def _repos(plan: ImportPlan) -> dict[str, RepositoryFields]:
    return {record.name: record.fields for record, _ in plan.repositories}


def test_a_variable_becomes_its_lowercased_hyphenated_secret_name() -> None:
    assert secret_name_of("BZ_FORGE_TOKEN") == "bz-forge-token"


def test_two_sources_naming_one_variable_share_one_secret() -> None:
    plan = _plan(_legacy(_source("a", "acme/a"), _source("b", "acme/b")), {"BZ_SHARED_TOKEN": "tok"})
    assert [secret.name.value for secret in plan.secrets] == ["bz-shared-token"]
    assert {record.fields.secret for record, _ in plan.work_sources} == {"bz-shared-token"}
    assert {record.fields.locator for record, _ in plan.work_sources} == {"acme/a", "acme/b"}


def test_every_change_is_the_migration_door_and_actor() -> None:
    plan = _plan(_legacy(_source("a", "acme/a")), {"BZ_SHARED_TOKEN": "tok", **_FORGE}, (CommitCoordinate(None, "w"),))
    changes = [s.change for s in plan.secrets] + [c for _, c in plan.work_sources] + [c for _, c in plan.repositories]
    assert {(change.actor, change.door) for change in changes} == {("migration", Door.MIGRATION)}
    assert plan.fact.actor == "migration"


def test_an_owned_origin_supplies_the_owner() -> None:
    plan = _plan(_legacy(), _FORGE, (CommitCoordinate("https://github.com/acme/widget.git", "widget"),))
    assert _repos(plan)["widget"].owner == "acme"


def test_a_bare_origin_falls_back_to_bz_forge_owner_then_blizzard() -> None:
    bare = (CommitCoordinate(None, "widget"),)
    assert _repos(_plan(_legacy(), {**_FORGE, "BZ_FORGE_OWNER": "ops"}, bare))["widget"].owner == "ops"
    assert _repos(_plan(_legacy(), _FORGE, bare))["widget"].owner == "blizzard"


def test_a_repository_takes_the_forge_url_base_branch_and_forge_token_secret() -> None:
    plan = _plan(_legacy(), _FORGE, (CommitCoordinate(None, "widget"),))
    assert _repos(plan)["widget"] == RepositoryFields(
        forge_api_url="https://api.github.com",
        owner="blizzard",
        repo="widget",
        base_branch="main",
        secret_name="bz-forge-token",
    )
    assert [secret.name.value for secret in plan.secrets] == ["bz-forge-token"]
    with_branch = _plan(_legacy(), {**_FORGE, "BZ_FORGE_BASE_BRANCH": "trunk"}, (CommitCoordinate(None, "widget"),))
    assert _repos(with_branch)["widget"].base_branch == "trunk"


def test_two_artifacts_on_one_coordinate_collapse_to_one_repository() -> None:
    commits = (
        CommitCoordinate("https://github.com/blizzard/widget.git", "widget"),
        CommitCoordinate(None, "widget"),
    )
    assert list(_repos(_plan(_legacy(), _FORGE, commits))) == ["widget"]


def test_coordinates_sharing_a_bare_name_each_take_owner_dash_repo() -> None:
    commits = (
        CommitCoordinate("https://github.com/acme/widget.git", "widget"),
        CommitCoordinate("git@github.com:ops/widget.git", "widget"),
        CommitCoordinate(None, "gadget"),
    )
    assert sorted(_repos(_plan(_legacy(), _FORGE, commits))) == ["acme-widget", "gadget", "ops-widget"]


def test_records_the_store_already_holds_are_skipped() -> None:
    held_source = ConfiguredWorkSource(
        name="other",
        fields=WorkSourceFields(
            provider="github", locator="acme/a", api_base=None, web_base=None, annotate=False, secret="x"
        ),
        revision=1,
        created_at=_AT,
        created_by="op",
        retired=True,
    )
    held_repo = ConfiguredRepository(
        name="mine",
        fields=RepositoryFields("https://api.github.com", "blizzard", "widget", "main", "x"),
        revision=1,
        created_at=_AT,
        created_by="op",
    )
    existing = ExistingConfig(
        secrets=frozenset({"bz-shared-token"}), work_sources=(held_source,), repositories=(held_repo,)
    )
    values = {"BZ_FORGE_URL": "https://api.github.com"}
    plan = _plan(_legacy(_source("a", "acme/a")), values, (CommitCoordinate(None, "widget"),), existing)
    assert plan.secrets == plan.work_sources == plan.repositories == ()
    assert set(plan.outcomes) == {
        ImportOutcome(RecordKind.SECRET, "bz-shared-token", created=False),
        ImportOutcome(RecordKind.WORK_SOURCE, "a", created=False),
        ImportOutcome(RecordKind.REPOSITORY, "mine", created=False),
    }


@pytest.mark.parametrize("value", [None, "", "   "])
def test_an_unset_or_blank_token_variable_is_refused_naming_it(value: str | None) -> None:
    values = {} if value is None else {"BZ_SHARED_TOKEN": value}
    with pytest.raises(LegacyImportRefused, match="BZ_SHARED_TOKEN"):
        _plan(_legacy(_source("a", "acme/a")), values)


def test_delivered_commits_without_a_forge_url_are_refused_naming_it() -> None:
    with pytest.raises(LegacyImportRefused, match="BZ_FORGE_URL"):
        _plan(_legacy(), {"BZ_FORGE_TOKEN": "t"}, (CommitCoordinate(None, "widget"),))


def test_repositories_to_write_without_a_forge_token_are_refused_naming_it() -> None:
    with pytest.raises(LegacyImportRefused, match="BZ_FORGE_TOKEN"):
        _plan(_legacy(), {"BZ_FORGE_URL": "https://api.github.com"}, (CommitCoordinate(None, "widget"),))


def test_a_block_failing_validation_is_refused_naming_it() -> None:
    with pytest.raises(LegacyImportRefused, match='"a"'):
        _plan(_legacy(_source("a", "not-a-locator")), {"BZ_SHARED_TOKEN": "tok"})


def test_the_fact_records_names_never_values() -> None:
    plan = _plan(_legacy(_source("a", "acme/a"), variables=("BZ_SHARED_TOKEN",)), {"BZ_SHARED_TOKEN": "tok"})
    assert plan.fact.read.sources == ("a",)
    assert plan.fact.read.variables == ("BZ_SHARED_TOKEN",)
    assert "tok" not in repr(plan.fact)


def test_legacy_start_refuses_before_an_import_naming_the_command() -> None:
    with pytest.raises(LegacyStartRefused, match=r"blizzard hub config import-legacy --dir /hub"):
        LegacyStart(recorded=False, keys=_legacy(variables=("BZ_FORGE_URL",))).check()


def test_legacy_start_refuses_after_an_import_naming_each_key_and_where() -> None:
    keys = _legacy(_source("a", "acme/a"), variables=("BZ_FORGE_URL",))
    with pytest.raises(LegacyStartRefused) as refused:
        LegacyStart(recorded=True, keys=keys).check()
    assert 'blizzard-hub.toml [[work_source]] "a"' in str(refused.value)
    assert "environment BZ_FORGE_URL" in str(refused.value)


@pytest.mark.parametrize("recorded", [True, False])
def test_legacy_start_starts_with_no_legacy_keys(recorded: bool) -> None:
    LegacyStart(recorded=recorded, keys=_legacy()).check()
