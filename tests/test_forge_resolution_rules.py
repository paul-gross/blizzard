"""The old-field rule: which forge a repository or work-source write names, read from ``forge`` first and the old
address fields after, and what an omitted field means on a create and on an edit (unit tier, pure)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forge_resolution import (
    REPOSITORY_FORGE_RULES,
    WORK_SOURCE_FORGE_RULES,
    ForgeChoice,
    ForgeConflict,
    ForgePlanner,
    ForgeRequired,
    ForgeRules,
    ForgeWanted,
    decide_forge,
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

pytestmark = pytest.mark.unit

_AT = datetime(2026, 10, 25, tzinfo=UTC)
_CTX = ChangeContext(actor="alice", door=Door.API)
_GHE = "https://ghe.acme.internal/api/v3"


def _forge(slug: str, api_base: str, *, web_base: str | None = None, retired: bool = False) -> ConfiguredForge:
    return ConfiguredForge(
        forge_id=ForgeId(f"frg_{slug}"),
        slug=slug,
        name=slug,
        kind="github",
        fields=ForgeFields(api_base, web_base),
        revision=1,
        created_at=_AT,
        created_by="op",
        retired=retired,
    )


_GITHUB = _forge("github", GITHUB_API_BASE)
_ENTERPRISE = _forge("ghe", _GHE)
_HELD = {forge.fields.api_base: forge for forge in (_GITHUB, _ENTERPRISE)}


def _decide(
    choice: ForgeChoice,
    rules: ForgeRules = WORK_SOURCE_FORGE_RULES,
    *,
    current: ConfiguredForge | None = None,
    named: ConfiguredForge | None = None,
):  # type: ignore[no-untyped-def]
    return decide_forge(choice, rules, provider="github", current=current, named=named, holder_of=_HELD.get)


def test_a_named_forge_is_used_and_no_field_warned_of() -> None:
    decision = _decide(ForgeChoice(forge="ghe"), named=_ENTERPRISE)
    assert decision is not None and (decision.forge, decision.old_fields) == (_ENTERPRISE, ())


@pytest.mark.parametrize("named", [None, _forge("old", "https://old.example", retired=True)])
def test_an_unknown_or_retired_named_forge_is_unavailable(named: ConfiguredForge | None) -> None:
    with pytest.raises(ForgeUnavailable):
        _decide(ForgeChoice(forge="old"), named=named)


def test_an_old_field_agreeing_with_the_named_forge_is_warned_of() -> None:
    decision = _decide(ForgeChoice(forge="ghe", api_base=f"{_GHE}/"), named=_ENTERPRISE)
    assert decision is not None and decision.old_fields == ("api_base",)


def test_an_old_field_disagreeing_with_the_named_forge_conflicts() -> None:
    with pytest.raises(ForgeConflict) as caught:
        _decide(ForgeChoice(forge="ghe", api_base=GITHUB_API_BASE), named=_ENTERPRISE)
    assert caught.value.field == "api_base"


def test_an_old_field_resolves_to_its_origins_forge_compared_canonical() -> None:
    decision = _decide(ForgeChoice(api_base="HTTPS://ghe.acme.internal:443/api/v3/"))
    assert decision is not None and decision.forge == _ENTERPRISE and decision.old_fields == ("api_base",)


def test_an_origin_no_forge_holds_is_wanted_with_the_web_origin_it_declares() -> None:
    decision = _decide(ForgeChoice(api_base="http://127.0.0.1:8123/", web_base="http://127.0.0.1:8124"))
    assert decision is not None
    assert decision.wanted == ForgeWanted("http://127.0.0.1:8123", "http://127.0.0.1:8124", "github")
    assert decision.old_fields == ("api_base", "web_base")


def test_an_origin_a_retired_forge_holds_is_unavailable_naming_it() -> None:
    retired = _forge("gone", "https://gone.example", retired=True)
    with pytest.raises(ForgeUnavailable, match="retired forge gone"):
        decide_forge(
            ForgeChoice(api_base="https://gone.example"),
            WORK_SOURCE_FORGE_RULES,
            provider="github",
            current=None,
            named=None,
            holder_of={"https://gone.example": retired}.get,
        )


@pytest.mark.parametrize("api_base", [None, "", "  "])
def test_a_work_source_given_no_address_is_on_github(api_base: str | None) -> None:
    decision = _decide(ForgeChoice(api_base=api_base), current=_ENTERPRISE)
    assert decision is not None and decision.forge == _GITHUB and decision.old_fields == ("api_base",)


def test_a_work_source_created_naming_nothing_is_on_github_with_a_warning() -> None:
    decision = _decide(ForgeChoice())
    assert decision is not None and decision.forge == _GITHUB and decision.old_fields == ("api_base",)


def test_an_edit_naming_nothing_keeps_the_forge_silently() -> None:
    assert _decide(ForgeChoice(), current=_ENTERPRISE) is None


def test_a_repository_created_naming_nothing_is_refused() -> None:
    with pytest.raises(ForgeRequired):
        _decide(ForgeChoice(), REPOSITORY_FORGE_RULES)


@pytest.mark.parametrize(("value", "message"), [(None, "must not be null"), (" ", "must not be blank")])
def test_a_repository_old_field_must_name_an_address(value: str | None, message: str) -> None:
    with pytest.raises(ConfigFieldError, match=message) as caught:
        _decide(ForgeChoice(api_base=value), REPOSITORY_FORGE_RULES, current=_GITHUB)
    assert caught.value.field == "forge_api_url"


def test_an_address_that_is_not_an_origin_is_refused_on_its_field() -> None:
    with pytest.raises(ConfigFieldError) as caught:
        _decide(ForgeChoice(api_base="ghe.acme"))
    assert caught.value.field == "api_base"


def test_a_web_base_disagreeing_with_the_forges_web_origin_conflicts_and_an_empty_one_is_the_forges() -> None:
    with pytest.raises(ForgeConflict, match=re.escape("https://ghe.acme.internal")):
        _decide(ForgeChoice(web_base="https://elsewhere.example"), current=_ENTERPRISE)
    agreeing = _decide(ForgeChoice(web_base="https://ghe.acme.internal/"), current=_ENTERPRISE)
    empty = _decide(ForgeChoice(web_base=""), current=_ENTERPRISE)
    assert agreeing is not None and empty is not None
    assert (agreeing.forge, empty.forge, empty.old_fields) == (_ENTERPRISE, _ENTERPRISE, ("web_base",))


def test_a_forge_of_another_kind_than_the_provider_mismatches() -> None:
    with pytest.raises(ForgeKindMismatch):
        decide_forge(
            ForgeChoice(forge="ghe"),
            WORK_SOURCE_FORGE_RULES,
            provider="gitlab",
            current=None,
            named=_ENTERPRISE,
            holder_of=_HELD.get,
        )


def test_a_planner_makes_one_forge_per_origin_however_it_is_spelled() -> None:
    planner = ForgePlanner([_GITHUB], _CTX, at=_AT, resolve=lambda _: None)
    first = planner.decide(
        ForgeChoice(api_base="https://new.example/"), WORK_SOURCE_FORGE_RULES, provider="github", current=None, index=0
    )
    second = planner.decide(
        ForgeChoice(api_base="https://NEW.example"), REPOSITORY_FORGE_RULES, provider=None, current=None, index=3
    )
    assert first is not None and second is not None and first.forge == second.forge
    assert [(forge.slug, index) for forge, _, index in planner.planned] == [("new-example", 0)]


def test_an_origins_slug_and_name_are_derived_as_the_migration_derives_them() -> None:
    assert origin_identity(GITHUB_API_BASE, None) == ("github", "GitHub")
    assert origin_identity(_GHE, None) == ("ghe-acme-internal", "ghe.acme.internal")
    assert origin_identity("http://127.0.0.1:8123", None) == ("127-0-0-1-8123", "127.0.0.1:8123")
    assert origin_identity("https://api.acme.example", "https://code.acme.example") == (
        "code-acme-example",
        "code.acme.example",
    )
    assert suffixed_slug("github", {"github", "github-2"}) == "github-3"
