"""Repository validation, sparse merge, and diff in isolation (unit tier)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.config.changes import ChangeContext, Door, FieldChange
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forges import ForgeId
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
    )
