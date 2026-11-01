"""Repository validation, sparse merge, and diff in isolation (unit tier)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.config.changes import ChangeContext, Door, FieldChange
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forges import ForgeId
from blizzard.hub.domain.config.landing_policies import LandingPolicy
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    RepositoryEdit,
    RepositoryFields,
    RepositoryName,
    RepositorySlug,
    diff,
    merge,
    validate_fields,
)

pytestmark = pytest.mark.unit

_FIELDS = RepositoryFields(
    forge_id=ForgeId("frg_github"), owner="acme", repo="demo", base_branch="master", secret_name="gh"
)


_CTX = ChangeContext(actor="usr_alice", door=Door.CLI)
_AT = datetime(2026, 10, 12, tzinfo=UTC)


def _record(slug: str = "demo", name: str | None = None) -> ConfiguredRepository:
    return ConfiguredRepository.new(slug, name, _FIELDS, _CTX, at=_AT)[0]


@pytest.mark.parametrize("slug", ["", "  ", "Demo", "a b", "a--b", "-a", "a-", "x" * 65])
def test_a_slug_outside_the_slug_grammar_is_refused_naming_the_field(slug: str) -> None:
    with pytest.raises(ConfigFieldError) as caught:
        RepositorySlug.parse(slug)
    assert caught.value.field == "slug"


@pytest.mark.parametrize("slug", ["blizzard", "a", "a-b-2", "x" * 64])
def test_a_slug_in_the_grammar_is_accepted(slug: str) -> None:
    assert RepositorySlug.parse(slug).value == slug


@pytest.mark.parametrize("name", ["", "  ", " padded", "x" * 129])
def test_a_blank_padded_or_long_display_name_is_refused_naming_the_field(name: str) -> None:
    with pytest.raises(ConfigFieldError) as caught:
        RepositoryName.parse(name)
    assert caught.value.field == "name"


@pytest.mark.parametrize("name", ["Acme Core", "a:b", "hub", "x" * 128])
def test_a_free_display_name_is_accepted(name: str) -> None:
    assert RepositoryName.parse(name).value == name


def test_a_new_repository_takes_its_slug_as_its_name_and_mints_its_id() -> None:
    record, change = ConfiguredRepository.new("demo", None, _FIELDS, _CTX, at=_AT)

    assert (record.slug, record.name, record.revision) == ("demo", "demo", 1)
    assert record.repository_id.startswith("repo_")
    assert (change.record_key, change.revision) == (record.repository_id, 1)


def test_an_edit_moves_the_slug_and_the_name_and_reports_the_slug_it_left() -> None:
    record = _record()

    decided = record.edit(RepositoryEdit(slug="core", name="Core"), _CTX, if_match=1, at=_AT)

    assert decided is not None
    edited, change = decided
    assert (edited.repository_id, edited.slug, edited.name, edited.revision) == (
        record.repository_id,
        "core",
        "Core",
        2,
    )
    assert record.slug_left_for(edited) == "demo"
    assert change.record_key == record.repository_id
    assert [(c.field, c.old, c.new) for c in change.diff] == [("slug", "demo", "core"), ("name", "demo", "Core")]


def test_an_edit_leaving_the_slug_reports_none_left() -> None:
    record = _record()

    decided = record.edit(RepositoryEdit(name="Core"), _CTX, if_match=None, at=_AT)

    assert decided is not None
    assert record.slug_left_for(decided[0]) is None


@pytest.mark.parametrize("field", ["slug", "name"])
def test_an_explicit_none_is_refused_on_the_slug_and_the_name(field: str) -> None:
    with pytest.raises(ConfigFieldError) as caught:
        _record().edit(RepositoryEdit(**{field: None}), _CTX, if_match=None, at=_AT)  # type: ignore[arg-type]
    assert caught.value.field == field


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"forge_id": ""}, "forge"),
        ({"owner": " "}, "owner"),
        ({"repo": ""}, "repo"),
        ({"base_branch": ""}, "base_branch"),
        ({"secret_name": ""}, "secret_name"),
    ],
)
def test_invalid_fields_name_the_offender(changes: dict[str, object], field: str) -> None:
    with pytest.raises(ConfigFieldError) as caught:
        validate_fields(replace(_FIELDS, **changes))  # type: ignore[arg-type]
    assert caught.value.field == field


def test_an_unset_edit_changes_nothing() -> None:
    assert merge(_FIELDS, RepositoryEdit()) == _FIELDS
    assert diff(_record(), _record()) == ()


def test_a_set_field_is_applied_and_the_rest_are_left() -> None:
    merged = merge(_FIELDS, RepositoryEdit(base_branch="main"))
    assert merged == replace(_FIELDS, base_branch="main")
    record = _record()
    assert diff(record, replace(record, fields=merged)) == (FieldChange("base_branch", "master", "main"),)


@pytest.mark.parametrize(
    ("field", "named"),
    [
        ("forge_id", "forge"),
        ("owner", "owner"),
        ("repo", "repo"),
        ("base_branch", "base_branch"),
        ("secret_name", "secret_name"),
    ],
)
def test_an_explicit_none_is_refused_on_every_field(field: str, named: str) -> None:
    with pytest.raises(ConfigFieldError) as caught:
        merge(_FIELDS, RepositoryEdit(**{field: None}))  # type: ignore[arg-type]
    assert caught.value.field == named


def test_a_create_diff_lists_every_field_against_none() -> None:
    assert diff(None, _record()) == (
        FieldChange("slug", None, "demo"),
        FieldChange("name", None, "demo"),
        FieldChange("forge", None, "frg_github"),
        FieldChange("owner", None, "acme"),
        FieldChange("repo", None, "demo"),
        FieldChange("base_branch", None, "master"),
        FieldChange("secret_name", None, "gh"),
        FieldChange("landing_policy", None, LandingPolicy.MERGE_ON_CHECKS),
    )


def test_a_repository_lands_by_merge_on_checks_with_no_timeout_unless_stated() -> None:
    assert (_FIELDS.landing_policy, _FIELDS.park_timeout_hours) == (LandingPolicy.MERGE_ON_CHECKS, None)


def test_an_unknown_policy_is_refused_naming_the_field() -> None:
    with pytest.raises(ConfigFieldError) as refused:
        validate_fields(replace(_FIELDS, landing_policy="queue"))  # pyright: ignore[reportArgumentType]
    assert refused.value.field == "landing_policy"
    assert "queue" in refused.value.message


@pytest.mark.parametrize("hours", [0, -1])
def test_a_park_timeout_below_one_hour_is_refused(hours: int) -> None:
    with pytest.raises(ConfigFieldError) as refused:
        validate_fields(replace(_FIELDS, park_timeout_hours=hours))
    assert refused.value.field == "park_timeout_hours"
    assert "at least 1" in refused.value.message


def test_a_park_timeout_on_a_repository_that_does_not_park_is_refused() -> None:
    with pytest.raises(ConfigFieldError) as refused:
        validate_fields(replace(_FIELDS, park_timeout_hours=5))
    assert refused.value.field == "park_timeout_hours"
    assert "parked" in refused.value.message


def test_merge_sets_the_policy_and_leaves_it_when_unset() -> None:
    assert merge(_FIELDS, RepositoryEdit(landing_policy=LandingPolicy.MERGE_ON_CHECKS)) == _FIELDS
    assert merge(_FIELDS, RepositoryEdit(owner="other")).landing_policy is LandingPolicy.MERGE_ON_CHECKS


def test_merge_refuses_clearing_the_policy() -> None:
    with pytest.raises(ConfigFieldError) as refused:
        merge(_FIELDS, RepositoryEdit(landing_policy=None))
    assert refused.value.field == "landing_policy"


def test_merge_clears_the_timeout_on_none() -> None:
    timed = replace(_FIELDS, park_timeout_hours=24)
    assert merge(timed, RepositoryEdit(park_timeout_hours=None)).park_timeout_hours is None


def test_merge_takes_a_stated_timeout_for_validation_to_judge() -> None:
    merged = merge(_FIELDS, RepositoryEdit(park_timeout_hours=5))
    assert merged.park_timeout_hours == 5
    with pytest.raises(ConfigFieldError):
        validate_fields(merged)


def test_merge_drops_a_held_timeout_once_the_policy_does_not_park() -> None:
    timed = replace(_FIELDS, park_timeout_hours=24)
    assert merge(timed, RepositoryEdit(landing_policy=LandingPolicy.MERGE_ON_CHECKS)).park_timeout_hours is None


def test_a_policy_or_timeout_edit_is_listed_in_the_diff() -> None:
    record = _record()
    timed = replace(record, fields=replace(record.fields, park_timeout_hours=24))
    assert diff(timed, record) == (FieldChange("park_timeout_hours", 24, None),)
