"""Work-source validation, sparse merge, and diff in isolation (unit tier)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.config.changes import ChangeContext, Door, FieldChange
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forges import ForgeId
from blizzard.hub.domain.config.work_sources import (
    WORK_SOURCE_KIND,
    ConfiguredWorkSource,
    WorkSourceEdit,
    WorkSourceFields,
    WorkSourceName,
    WorkSourceSlug,
    diff,
    merge,
    validate_fields,
)

pytestmark = pytest.mark.unit

_FIELDS = WorkSourceFields(
    provider="github", locator="acme/demo", forge_id=ForgeId("frg_github"), annotate=False, secret="gh"
)
_CTX = ChangeContext(actor="usr_alice", door=Door.CLI)
_AT = datetime(2026, 10, 12, tzinfo=UTC)


def _record(slug: str = "demo", name: str | None = None) -> ConfiguredWorkSource:
    return ConfiguredWorkSource.new(slug, name, _FIELDS, _CTX, at=_AT)[0]


@pytest.mark.parametrize("slug", ["", "  ", "Demo", "a:b", "a#b", "a--b", "-a", "x" * 65])
def test_a_slug_outside_the_slug_grammar_is_refused_naming_the_field(slug: str) -> None:
    with pytest.raises(ConfigFieldError) as caught:
        WorkSourceSlug.parse(slug)
    assert caught.value.field == "slug"


def test_hub_is_never_a_work_source_slug() -> None:
    with pytest.raises(ConfigFieldError) as caught:
        WorkSourceSlug.parse("hub")
    assert caught.value.field == "slug"
    assert WORK_SOURCE_KIND.slug_of("hub") is None


def test_an_id_never_parses_as_a_slug() -> None:
    assert WORK_SOURCE_KIND.slug_of(_record().work_source_id) is None


@pytest.mark.parametrize("name", ["", "  ", " padded", "x" * 129])
def test_a_blank_padded_or_long_display_name_is_refused_naming_the_field(name: str) -> None:
    with pytest.raises(ConfigFieldError) as caught:
        WorkSourceName.parse(name)
    assert caught.value.field == "name"


@pytest.mark.parametrize("name", ["Acme Core", "a:b", "hub"])
def test_a_free_display_name_is_accepted(name: str) -> None:
    assert WorkSourceName.parse(name).value == name


def test_a_new_work_source_takes_its_slug_as_its_name_and_mints_its_id() -> None:
    record, change = ConfiguredWorkSource.new("demo", None, _FIELDS, _CTX, at=_AT)

    assert (record.slug, record.name, record.revision) == ("demo", "demo", 1)
    assert record.work_source_id.startswith("wsrc_")
    assert (change.record_key, change.revision) == (record.work_source_id, 1)


def test_an_edit_moves_the_slug_and_the_name_and_reports_the_slug_it_left() -> None:
    record = _record()

    decided = record.edit(WorkSourceEdit(slug="core", name="Core"), _CTX, if_match=1, at=_AT)

    assert decided is not None
    edited, change = decided
    assert (edited.work_source_id, edited.slug, edited.name, edited.revision) == (
        record.work_source_id,
        "core",
        "Core",
        2,
    )
    assert record.slug_left_for(edited) == "demo"
    assert change.record_key == record.work_source_id
    assert [(c.field, c.old, c.new) for c in change.diff] == [("slug", "demo", "core"), ("name", "demo", "Core")]


def test_an_edit_to_hub_is_refused_naming_the_slug() -> None:
    with pytest.raises(ConfigFieldError) as caught:
        _record().edit(WorkSourceEdit(slug="hub"), _CTX, if_match=None, at=_AT)
    assert caught.value.field == "slug"


@pytest.mark.parametrize("field", ["slug", "name"])
def test_an_explicit_none_is_refused_on_the_slug_and_the_name(field: str) -> None:
    with pytest.raises(ConfigFieldError) as caught:
        _record().edit(WorkSourceEdit(**{field: None}), _CTX, if_match=None, at=_AT)  # type: ignore[arg-type]
    assert caught.value.field == field


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
    with pytest.raises(ConfigFieldError) as caught:
        validate_fields(replace(_FIELDS, **changes))  # type: ignore[arg-type]
    assert caught.value.field == field


def test_an_unset_edit_changes_nothing() -> None:
    assert merge(_FIELDS, WorkSourceEdit()) == _FIELDS
    assert diff(_record(), _record()) == ()


def test_an_explicit_none_clears_a_nullable_field_and_an_absent_one_is_left() -> None:
    merged = merge(_FIELDS, WorkSourceEdit(secret=None, annotate=True))
    assert merged.secret is None
    assert merged.annotate is True
    assert merged.locator == _FIELDS.locator


@pytest.mark.parametrize("field", ["provider", "locator", "annotate"])
def test_none_on_a_non_nullable_field_is_refused_naming_it(field: str) -> None:
    with pytest.raises(ConfigFieldError) as caught:
        merge(_FIELDS, WorkSourceEdit(**{field: None}))  # type: ignore[arg-type]
    assert caught.value.field == field


def test_diff_lists_only_changed_fields_by_wire_name() -> None:
    record = _record()
    new = replace(record, fields=merge(_FIELDS, WorkSourceEdit(annotate=True, secret="other")))
    assert diff(record, new) == (FieldChange("annotate", False, True), FieldChange("secret", "gh", "other"))


def test_a_create_diff_lists_every_set_field_against_null() -> None:
    assert diff(None, _record()) == (
        FieldChange("slug", None, "demo"),
        FieldChange("name", None, "demo"),
        FieldChange("provider", None, "github"),
        FieldChange("locator", None, "acme/demo"),
        FieldChange("forge", None, "frg_github"),
        FieldChange("annotate", None, False),
        FieldChange("secret", None, "gh"),
    )
