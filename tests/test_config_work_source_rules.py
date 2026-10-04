"""Work-source validation, sparse merge, and diff in isolation (unit tier)."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.config.changes import FieldChange
from blizzard.hub.domain.config.work_sources import (
    ConfigFieldError,
    WorkSourceEdit,
    WorkSourceFields,
    diff,
    merge,
    validate_fields,
    validate_name,
)

pytestmark = pytest.mark.unit

_FIELDS = WorkSourceFields(
    provider="github", locator="acme/demo", api_base=None, web_base="https://gh.example", annotate=False, secret="gh"
)


@pytest.mark.parametrize("name", ["", "  ", "a:b", "hub"])
def test_a_bad_name_is_refused_naming_the_field(name: str) -> None:
    with pytest.raises(ConfigFieldError) as caught:
        validate_name(name)
    assert caught.value.field == "name"


def test_a_plain_name_is_accepted() -> None:
    validate_name("demo-1")


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"provider": "svn"}, "provider"),
        ({"locator": "no-slash"}, "locator"),
        ({"locator": "a/b/c"}, "locator"),
        ({"secret": None}, "secret"),
    ],
)
def test_invalid_fields_name_the_offender(changes: dict[str, object], field: str) -> None:
    from dataclasses import replace

    with pytest.raises(ConfigFieldError) as caught:
        validate_fields(replace(_FIELDS, **changes))  # type: ignore[arg-type]
    assert caught.value.field == field


def test_an_unset_edit_changes_nothing() -> None:
    assert merge(_FIELDS, WorkSourceEdit()) == _FIELDS
    assert diff(_FIELDS, merge(_FIELDS, WorkSourceEdit())) == ()


def test_an_explicit_none_clears_a_nullable_field_and_an_absent_one_is_left() -> None:
    merged = merge(_FIELDS, WorkSourceEdit(web_base=None, annotate=True))
    assert merged.web_base is None
    assert merged.annotate is True
    assert merged.locator == _FIELDS.locator


@pytest.mark.parametrize("field", ["provider", "locator", "annotate"])
def test_none_on_a_non_nullable_field_is_refused_naming_it(field: str) -> None:
    with pytest.raises(ConfigFieldError) as caught:
        merge(_FIELDS, WorkSourceEdit(**{field: None}))  # type: ignore[arg-type]
    assert caught.value.field == field


def test_diff_lists_only_changed_fields_by_wire_name() -> None:
    new = merge(_FIELDS, WorkSourceEdit(annotate=True, secret="other"))
    assert diff(_FIELDS, new) == (FieldChange("annotate", False, True), FieldChange("secret", "gh", "other"))


def test_a_create_diff_lists_every_set_field_against_null() -> None:
    assert diff(None, _FIELDS) == (
        FieldChange("provider", None, "github"),
        FieldChange("locator", None, "acme/demo"),
        FieldChange("web_base", None, "https://gh.example"),
        FieldChange("annotate", None, False),
        FieldChange("secret", None, "gh"),
    )
