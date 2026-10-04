"""Repository validation, sparse merge, and diff in isolation (unit tier)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from blizzard.hub.domain.config.changes import FieldChange
from blizzard.hub.domain.config.repositories import (
    RepositoryEdit,
    RepositoryFields,
    diff,
    merge,
    validate_fields,
    validate_name,
)
from blizzard.hub.domain.config.work_sources import ConfigFieldError

pytestmark = pytest.mark.unit

_FIELDS = RepositoryFields(
    forge_api_url="https://api.github.com", owner="acme", repo="demo", base_branch="master", secret_name="gh"
)


@pytest.mark.parametrize("name", ["", "  "])
def test_a_blank_name_is_refused_naming_the_field(name: str) -> None:
    with pytest.raises(ConfigFieldError) as caught:
        validate_name(name)
    assert caught.value.field == "name"


@pytest.mark.parametrize("name", ["blizzard", "a:b", "hub"])
def test_names_the_work_source_rules_refuse_are_accepted(name: str) -> None:
    validate_name(name)


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"forge_api_url": ""}, "forge_api_url"),
        ({"forge_api_url": "api.github.com"}, "forge_api_url"),
        ({"forge_api_url": "ftp://forge.example"}, "forge_api_url"),
        ({"forge_api_url": "https://"}, "forge_api_url"),
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


@pytest.mark.parametrize("url", ["https://api.github.com", "http://localhost:8080/api/v3"])
def test_an_absolute_http_url_is_accepted(url: str) -> None:
    validate_fields(replace(_FIELDS, forge_api_url=url))


def test_an_unset_edit_changes_nothing() -> None:
    assert merge(_FIELDS, RepositoryEdit()) == _FIELDS
    assert diff(_FIELDS, _FIELDS) == ()


def test_a_set_field_is_applied_and_the_rest_are_left() -> None:
    merged = merge(_FIELDS, RepositoryEdit(base_branch="main"))
    assert merged == replace(_FIELDS, base_branch="main")
    assert diff(_FIELDS, merged) == (FieldChange("base_branch", "master", "main"),)


@pytest.mark.parametrize("field", ["forge_api_url", "owner", "repo", "base_branch", "secret_name"])
def test_an_explicit_none_is_refused_on_every_field(field: str) -> None:
    with pytest.raises(ConfigFieldError) as caught:
        merge(_FIELDS, RepositoryEdit(**{field: None}))  # type: ignore[arg-type]
    assert caught.value.field == field


def test_a_create_diff_lists_every_field_against_none() -> None:
    assert diff(None, _FIELDS) == (
        FieldChange("forge_api_url", None, "https://api.github.com"),
        FieldChange("owner", None, "acme"),
        FieldChange("repo", None, "demo"),
        FieldChange("base_branch", None, "master"),
        FieldChange("secret_name", None, "gh"),
    )
