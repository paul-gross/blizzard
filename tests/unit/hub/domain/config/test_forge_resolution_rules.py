"""Which forge a repository or work-source write names: its ``forge`` reference, never an address field — the forge
an address field belongs to is named in the refusal — and, for the legacy import alone, the forge an origin names
(unit tier, pure)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.config.apply import REPOSITORIES_SECTION, WORK_SOURCES_SECTION
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.config.forge_resolution import (
    FieldMovedToForge,
    ForgeChoice,
    ForgeRequired,
    MovedField,
    resolve_forge,
)
from blizzard.hub.domain.config.forges import (
    GITHUB_API_BASE,
    ConfiguredForge,
    ForgeFields,
    ForgeId,
    ForgeKindMismatch,
    ForgeUnavailable,
    origin_identity,
    suffixed_slug,
)
from blizzard.hub.domain.config.origin_forges import (
    REPOSITORY_ORIGIN_RULES,
    WORK_SOURCE_ORIGIN_RULES,
    OriginChoice,
    OriginConflict,
    OriginPlanner,
)

_AT = datetime(2026, 10, 25, tzinfo=UTC)
_CTX = ChangeContext(actor="alice", door=Door.API)
_GHE = "https://ghe.acme.internal/api/v3"


def _forge(
    slug: str, api_base: str, *, web_base: str | None = None, retired: bool = False, kind: str = "github"
) -> ConfiguredForge:
    return ConfiguredForge(
        forge_id=ForgeId(f"frg_{slug}"),
        slug=slug,
        name=slug,
        kind=kind,
        fields=ForgeFields(api_base, web_base),
        revision=1,
        created_at=_AT,
        created_by="op",
        retired=retired,
    )


_GITHUB = _forge("github", GITHUB_API_BASE)
_ENTERPRISE = _forge("ghe", _GHE)
_FORGES = (_GITHUB, _ENTERPRISE)


def _resolve(
    choice: ForgeChoice, *, current: ConfiguredForge | None = None, provider: str | None = "github"
) -> ConfiguredForge | None:
    return resolve_forge(choice, provider=provider, current=current, creating=current is None, forges=lambda: _FORGES)


def _moved(*fields: tuple[str, str | None]) -> tuple[MovedField, ...]:
    return tuple(MovedField(field, value) for field, value in fields)


def test_a_named_forge_is_the_records() -> None:
    assert _resolve(ForgeChoice(forge="ghe", named=_ENTERPRISE)) == _ENTERPRISE


@pytest.mark.parametrize("named", [None, _forge("old", "https://old.example", retired=True)])
def test_an_unknown_or_retired_named_forge_is_unavailable(named: ConfiguredForge | None) -> None:
    with pytest.raises(ForgeUnavailable):
        _resolve(ForgeChoice(forge="old", named=named))


def test_a_create_naming_no_forge_is_refused_for_either_kind() -> None:
    with pytest.raises(ForgeRequired) as source:
        _resolve(ForgeChoice())
    with pytest.raises(ForgeRequired):
        _resolve(ForgeChoice(), provider=None)
    assert source.value.field == "forge"


def test_an_edit_naming_no_forge_keeps_the_records() -> None:
    assert _resolve(ForgeChoice(), current=_ENTERPRISE) is None


def test_a_forge_of_another_kind_than_the_provider_mismatches() -> None:
    with pytest.raises(ForgeKindMismatch):
        _resolve(ForgeChoice(forge="other", named=_forge("other", "https://other.example", kind="gitlab")))


def test_a_moved_field_names_the_forge_the_write_names_first() -> None:
    choice = ForgeChoice(forge="ghe", named=_ENTERPRISE, moved=_moved(("api_base", GITHUB_API_BASE)))
    with pytest.raises(FieldMovedToForge) as caught:
        _resolve(choice, current=_GITHUB)
    assert caught.value.field == "api_base"
    assert "forge ghe" in caught.value.message
    assert caught.value.code == "field_moved_to_forge"


def test_a_moved_field_on_an_edit_names_the_records_forge() -> None:
    with pytest.raises(FieldMovedToForge, match="forge ghe"):
        _resolve(ForgeChoice(moved=_moved(("web_base", "https://elsewhere.example"))), current=_ENTERPRISE)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("forge_api_url", "HTTPS://ghe.acme.internal:443/api/v3/"),
        ("api_base", _GHE),
        ("web_base", "https://ghe.acme.internal/"),
    ],
)
def test_a_moved_field_on_a_create_names_the_forge_holding_its_origin_compared_canonical(
    field: str, value: str
) -> None:
    with pytest.raises(FieldMovedToForge, match="forge ghe") as caught:
        _resolve(ForgeChoice(moved=_moved((field, value))))
    assert caught.value.field == field


@pytest.mark.parametrize("value", ["https://nobody.example", "not an origin", None])
def test_a_moved_field_no_forge_holds_names_the_field_and_forge_only(value: str | None) -> None:
    with pytest.raises(FieldMovedToForge) as caught:
        _resolve(ForgeChoice(moved=_moved(("forge_api_url", value))), provider=None)
    assert caught.value.field == "forge_api_url"
    assert "`forge`" in caught.value.message
    assert "forge ghe" not in caught.value.message and "forge github" not in caught.value.message


def test_a_moved_field_is_refused_before_an_unknown_forge() -> None:
    choice = ForgeChoice(forge="nope", named=None, moved=_moved(("api_base", _GHE)))
    with pytest.raises(FieldMovedToForge, match="forge ghe"):
        _resolve(choice)


def test_a_planner_makes_one_forge_per_origin_however_it_is_spelled() -> None:
    planner = OriginPlanner([_GITHUB], _CTX, at=_AT)
    first = planner.decide(
        OriginChoice(api_base="https://new.example/"),
        WORK_SOURCE_ORIGIN_RULES,
        provider="github",
        section=WORK_SOURCES_SECTION,
        index=0,
    )
    second = planner.decide(
        OriginChoice(api_base="https://NEW.example"),
        REPOSITORY_ORIGIN_RULES,
        provider=None,
        section=REPOSITORIES_SECTION,
        index=3,
    )
    assert first == second
    assert [(p.record.slug, p.section, p.index) for p in planner.planned] == [("new-example", WORK_SOURCES_SECTION, 0)]


@pytest.mark.parametrize("bare_first", [True, False])
def test_a_planned_forge_takes_the_web_origin_an_entry_declares_whichever_entry_comes_first(bare_first: bool) -> None:
    bare = OriginChoice(api_base="https://api.acme.example")
    declared = OriginChoice(api_base="https://api.acme.example", web_base="https://code.acme.example/")
    entries = [bare, declared] if bare_first else [declared, bare]
    planner = OriginPlanner([_GITHUB], _CTX, at=_AT)
    for entry in entries:
        planner.foresee(entry, WORK_SOURCE_ORIGIN_RULES)
    resolved = [
        planner.decide(entry, WORK_SOURCE_ORIGIN_RULES, provider="github", section=WORK_SOURCES_SECTION, index=index)
        for index, entry in enumerate(entries)
    ]
    (planned,) = planner.planned
    assert planned.record.fields.web_base == "https://code.acme.example"
    assert planned.record.slug == "code-acme-example"
    assert all(r == planned.record for r in resolved)


def test_a_second_web_origin_declared_for_one_planned_forge_conflicts() -> None:
    planner = OriginPlanner([_GITHUB], _CTX, at=_AT)
    entries = [
        OriginChoice(api_base="https://api.acme.example", web_base="https://code.acme.example"),
        OriginChoice(api_base="https://api.acme.example", web_base="https://elsewhere.example"),
    ]
    for entry in entries:
        planner.foresee(entry, WORK_SOURCE_ORIGIN_RULES)
    planner.decide(entries[0], WORK_SOURCE_ORIGIN_RULES, provider="github", section=WORK_SOURCES_SECTION, index=0)
    with pytest.raises(OriginConflict, match=re.escape("https://code.acme.example")):
        planner.decide(entries[1], WORK_SOURCE_ORIGIN_RULES, provider="github", section=WORK_SOURCES_SECTION, index=1)


def test_an_origins_slug_and_name_are_derived_as_the_migration_derives_them() -> None:
    assert origin_identity(GITHUB_API_BASE, None) == ("github", "GitHub")
    assert origin_identity(_GHE, None) == ("ghe-acme-internal", "ghe.acme.internal")
    assert origin_identity("http://127.0.0.1:8123", None) == ("127-0-0-1-8123", "127.0.0.1:8123")
    assert origin_identity("https://api.acme.example", "https://code.acme.example") == (
        "code-acme-example",
        "code.acme.example",
    )
    assert suffixed_slug("github", {"github", "github-2"}) == "github-3"
