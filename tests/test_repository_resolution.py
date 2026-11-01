"""Resolving a chunk's commit pointers to repository records — the pure domain functions (unit tier)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeFields, ForgeId
from blizzard.hub.domain.config.repositories import (
    CommitOrigin,
    ConfiguredRepository,
    RepositoryFields,
    RepositoryId,
    RepositoryOutcome,
    resolve_chunk_repositories,
    resolve_origin_record,
    resolve_repository,
)

pytestmark = pytest.mark.unit

_MINTED = datetime(2026, 3, 1, tzinfo=UTC)
_API = "https://api.github.com"


def _forge(api_base: str, web_base: str | None = None) -> ConfiguredForge:
    return ConfiguredForge(
        forge_id=ForgeId(f"frg_{api_base}"),
        slug="forge",
        name="forge",
        kind="github",
        fields=ForgeFields(api_base, web_base),
        revision=1,
        created_at=_MINTED,
        created_by="op",
    )


#: The forges the rows below name, one per API origin, as a tenant holds them.
_FORGES = [
    _forge(_API),
    _forge("https://ghe.example.com/api/v3"),
    _forge("https://api.acme.example", "https://code.acme.example"),
]


def _id(slug: str) -> RepositoryId:
    return RepositoryId(f"repo_{slug}")


def _row(
    name: str,
    *,
    owner: str = "acme",
    repo: str | None = None,
    forge: str = _API,
    base: str = "main",
    secret: str = "tok",
    retired_at: datetime | None = None,
) -> ConfiguredRepository:
    return ConfiguredRepository(
        repository_id=_id(name),
        slug=name,
        name=name,
        fields=RepositoryFields(
            forge_id=ForgeId(f"frg_{forge}"), owner=owner, repo=repo or name, base_branch=base, secret_name=secret
        ),
        revision=1,
        created_at=_MINTED - timedelta(days=30),
        created_by="op",
        retired=retired_at is not None,
        retired_at=retired_at,
    )


def _resolve(origins: list[CommitOrigin], rows: list[ConfiguredRepository]):  #
    return resolve_chunk_repositories(origins, rows, _FORGES, minted_at=_MINTED, linked=None)


def _in_project(origins: list[CommitOrigin], rows: list[ConfiguredRepository], *linked: str):  #
    return resolve_chunk_repositories(
        origins, rows, _FORGES, minted_at=_MINTED, linked=frozenset(_id(n) for n in linked), project="widgets"
    )


def test_an_exact_origin_resolves_by_host_owner_and_name() -> None:
    rows = [_row("widget"), _row("widget-fork", owner="other", repo="widget")]

    result = _resolve([CommitOrigin.of("https://github.com/other/widget.git", "widget")], rows)

    assert result.outcome is RepositoryOutcome.RESOLVED
    assert [t.owner for t in result.targets] == ["other"]
    assert result.qualified == ("other/widget",)
    assert result.forge_of(result.targets[0]).fields.api_base == _API


def test_an_origin_on_a_forges_own_web_origin_resolves_to_its_records() -> None:
    rows = [_row("widget", forge="https://api.acme.example")]

    on_web = _resolve([CommitOrigin.of("https://code.acme.example/acme/widget.git", "widget")], rows)
    on_api = _resolve([CommitOrigin.of("https://api.acme.example/acme/widget.git", "widget")], rows)

    assert on_web.outcome is RepositoryOutcome.RESOLVED and on_web.records == (_id("widget"),)
    assert on_api.outcome is RepositoryOutcome.RESOLVED


def test_a_file_origin_carries_no_owner_so_it_resolves_by_bare_name() -> None:
    result = _resolve([CommitOrigin.of("file:///srv/fixtures/widget.git", "widget")], [_row("widget")])

    assert result.outcome is RepositoryOutcome.RESOLVED
    assert result.qualified == ("acme/widget",)


def test_a_unique_repo_name_resolves_whatever_the_record_is_called() -> None:
    result = _resolve([CommitOrigin.of(None, "widget")], [_row("the-widget", repo="widget"), _row("gadget")])

    assert result.outcome is RepositoryOutcome.RESOLVED
    assert result.qualified == ("acme/widget",)


def test_an_ambiguous_repo_name_is_unresolved() -> None:
    rows = [_row("a-widget", repo="widget"), _row("b-widget", owner="other", repo="widget")]

    result = _resolve([CommitOrigin.of(None, "widget")], rows)

    assert result.outcome is RepositoryOutcome.UNRESOLVED
    assert "a-widget" in result.detail and "b-widget" in result.detail


def test_an_origin_on_another_forge_is_unresolved() -> None:
    result = _resolve([CommitOrigin.of("https://git.example.com/acme/widget", "widget")], [_row("widget")])

    assert result.outcome is RepositoryOutcome.UNRESOLVED


def test_a_bare_name_no_record_carries_is_unresolved() -> None:
    result = _resolve([CommitOrigin.of(None, "gadget")], [_row("widget")])

    assert result.outcome is RepositoryOutcome.UNRESOLVED
    assert "gadget" in result.detail


def test_no_commit_pointers_is_nothing_to_resolve_not_a_refusal() -> None:
    assert _resolve([], []).outcome is RepositoryOutcome.NOTHING_TO_RESOLVE


def test_a_repository_retired_before_the_chunk_was_minted_is_unresolved() -> None:
    row = _row("widget", retired_at=_MINTED - timedelta(seconds=1))

    assert _resolve([CommitOrigin.of(None, "widget")], [row]).outcome is RepositoryOutcome.UNRESOLVED


def test_a_repository_retired_after_the_chunk_was_minted_still_resolves() -> None:
    row = _row("widget", retired_at=_MINTED + timedelta(seconds=1))

    assert _resolve([CommitOrigin.of(None, "widget")], [row]).outcome is RepositoryOutcome.RESOLVED


@pytest.mark.parametrize(
    ("field", "other"),
    [
        ("forge", {"forge": "https://ghe.example.com/api/v3"}),
        ("owner", {"owner": "other"}),
        ("base_branch", {"base": "develop"}),
        ("secret_name", {"secret": "other-tok"}),
    ],
)
def test_records_that_disagree_on_a_landing_field_each_resolve_to_their_own_target(
    field: str, other: dict[str, Any]
) -> None:
    rows = [_row("widget"), _row("gadget", **other)]
    origins = [CommitOrigin.of(None, "widget"), CommitOrigin.of(None, "gadget")]

    result = _resolve(origins, rows)

    assert result.outcome is RepositoryOutcome.RESOLVED
    assert [t.record for t in result.targets] == [_id("widget"), _id("gadget")]
    assert result.targets[0] != result.targets[1]


def test_records_on_one_forge_each_resolve_qualifying_each_commit() -> None:
    rows = [_row("widget"), _row("gadget")]
    origins = [CommitOrigin.of(None, "widget"), CommitOrigin.of(None, "gadget")]

    result = _resolve(origins, rows)

    assert result.outcome is RepositoryOutcome.RESOLVED
    assert result.qualified == ("acme/widget", "acme/gadget")
    assert result.records == (_id("widget"), _id("gadget"))
    assert {t.forge_id for t in result.targets} == {ForgeId(f"frg_{_API}")}


def test_a_point_in_time_lookup_never_matches_a_retired_record() -> None:
    rows = [_row("widget", retired_at=_MINTED + timedelta(days=1))]

    assert resolve_repository(CommitOrigin.of(None, "widget"), rows, _FORGES, linked=None) is None
    assert resolve_repository(CommitOrigin.of(None, "widget"), [_row("widget")], _FORGES, linked=None) is not None


def test_a_point_in_time_lookup_matches_only_what_the_project_links() -> None:
    rows = [_row("a-widget", repo="widget"), _row("b-widget", owner="other", repo="widget")]
    origin = CommitOrigin.of(None, "widget")

    assert resolve_repository(origin, rows, _FORGES, linked=None) is None
    assert resolve_repository(origin, rows, _FORGES, linked=frozenset({_id("gadget")})) is None
    found = resolve_repository(origin, rows, _FORGES, linked=frozenset({_id("b-widget")}))
    assert found is not None and found.name == "b-widget"


def test_narrowing_to_the_project_resolves_a_name_ambiguous_across_the_tenant() -> None:
    rows = [_row("a-widget", repo="widget"), _row("b-widget", owner="other", repo="widget")]

    result = _in_project([CommitOrigin.of(None, "widget")], rows, "b-widget")

    assert result.outcome is RepositoryOutcome.RESOLVED
    assert result.qualified == ("other/widget",)
    assert result.records == (_id("b-widget"),)


def test_a_pointer_whose_one_tenant_record_the_project_does_not_link_is_not_linked() -> None:
    rows = [_row("the-widget", repo="widget"), _row("gadget")]

    result = _in_project([CommitOrigin.of(None, "widget")], rows, "gadget")

    assert result.outcome is RepositoryOutcome.NOT_LINKED
    assert result.targets == () and result.records == ()
    assert "the-widget" in result.detail and "widgets" in result.detail


def test_a_pointer_no_tenant_record_matches_stays_unresolved_under_a_project() -> None:
    result = _in_project([CommitOrigin.of(None, "gizmo")], [_row("widget")], "widget")

    assert result.outcome is RepositoryOutcome.UNRESOLVED
    assert "gizmo" in result.detail


def test_a_name_ambiguous_across_the_tenant_and_linked_nowhere_is_unresolved() -> None:
    rows = [_row("a-widget", repo="widget"), _row("b-widget", owner="other", repo="widget"), _row("gadget")]

    result = _in_project([CommitOrigin.of(None, "widget")], rows, "gadget")

    assert result.outcome is RepositoryOutcome.UNRESOLVED
    assert "a-widget" in result.detail and "b-widget" in result.detail and "widgets" in result.detail


def test_a_name_ambiguous_among_the_projects_own_links_is_unresolved() -> None:
    rows = [_row("a-widget", repo="widget"), _row("b-widget", owner="other", repo="widget")]

    result = _in_project([CommitOrigin.of(None, "widget")], rows, "a-widget", "b-widget")

    assert result.outcome is RepositoryOutcome.UNRESOLVED
    assert "a-widget" in result.detail and "b-widget" in result.detail


def test_a_linked_record_retired_before_the_chunk_was_minted_is_unresolved_not_unlinked() -> None:
    row = _row("widget", retired_at=_MINTED - timedelta(seconds=1))

    assert _in_project([CommitOrigin.of(None, "widget")], [row], "widget").outcome is RepositoryOutcome.UNRESOLVED
    assert _in_project([CommitOrigin.of(None, "widget")], [row]).outcome is RepositoryOutcome.UNRESOLVED


def test_an_unlinked_pointer_refuses_before_the_records_are_compared() -> None:
    rows = [_row("widget"), _row("gadget", owner="other")]
    origins = [CommitOrigin.of(None, "widget"), CommitOrigin.of(None, "gadget")]

    result = _in_project(origins, rows, "widget")

    assert result.outcome is RepositoryOutcome.NOT_LINKED
    assert "gadget" in result.detail


def test_with_no_project_narrowing_a_pointer_is_never_refused_as_not_linked() -> None:
    result = _resolve([CommitOrigin.of(None, "widget")], [_row("the-widget", repo="widget")])

    assert result.outcome is RepositoryOutcome.RESOLVED
    assert result.records == (_id("the-widget"),)


def test_an_origin_record_is_the_one_the_chunks_project_links_and_that_stands_for_it() -> None:
    rows = [
        _row("a-widget", repo="widget"),
        _row("b-widget", owner="other", repo="widget", retired_at=_MINTED + timedelta(days=1)),
        _row("old-gadget", repo="gadget", retired_at=_MINTED - timedelta(days=1)),
    ]

    def record(name: str, *linked: str) -> str | None:
        found = resolve_origin_record(
            CommitOrigin.of(None, name), rows, _FORGES, minted_at=_MINTED, linked=frozenset(_id(n) for n in linked)
        )
        return None if found is None else found.name

    assert record("widget", "b-widget") == "b-widget"
    assert record("widget", "a-widget", "b-widget") is None
    assert record("widget") is None
    assert record("gadget", "old-gadget") is None
    assert resolve_origin_record(CommitOrigin.of(None, "widget"), rows, _FORGES, minted_at=_MINTED, linked=None) is None
