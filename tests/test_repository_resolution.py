"""Resolving a chunk's commit pointers to repository records — the pure domain functions (unit tier)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from blizzard.hub.domain.config.repositories import (
    CommitOrigin,
    ConfiguredRepository,
    RepositoryFields,
    RepositoryOutcome,
    resolve_chunk_repositories,
    resolve_repository,
)

pytestmark = pytest.mark.unit

_MINTED = datetime(2026, 3, 1, tzinfo=UTC)
_API = "https://api.github.com"


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
        name=name,
        fields=RepositoryFields(
            forge_api_url=forge, owner=owner, repo=repo or name, base_branch=base, secret_name=secret
        ),
        revision=1,
        created_at=_MINTED - timedelta(days=30),
        created_by="op",
        retired=retired_at is not None,
        retired_at=retired_at,
    )


def _resolve(origins: list[CommitOrigin], rows: list[ConfiguredRepository]):  # type: ignore[no-untyped-def]
    return resolve_chunk_repositories(origins, rows, minted_at=_MINTED)


def test_an_exact_origin_resolves_by_host_owner_and_name() -> None:
    rows = [_row("widget"), _row("widget-fork", owner="other", repo="widget")]

    result = _resolve([CommitOrigin.of("https://github.com/other/widget.git", "widget")], rows)

    assert result.outcome is RepositoryOutcome.RESOLVED
    assert result.target is not None and result.target.owner == "other"
    assert result.qualified == ("other/widget",)


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
def test_records_that_disagree_on_a_landing_field_are_refused(field: str, other: dict[str, Any]) -> None:
    rows = [_row("widget"), _row("gadget", **other)]
    origins = [CommitOrigin.of(None, "widget"), CommitOrigin.of(None, "gadget")]

    result = _resolve(origins, rows)

    assert result.outcome is RepositoryOutcome.DISAGREE
    assert "widget" in result.detail and "gadget" in result.detail


def test_records_that_agree_resolve_to_one_target_qualifying_each_commit() -> None:
    rows = [_row("widget"), _row("gadget")]
    origins = [CommitOrigin.of(None, "widget"), CommitOrigin.of(None, "gadget")]

    result = _resolve(origins, rows)

    assert result.outcome is RepositoryOutcome.RESOLVED
    assert result.qualified == ("acme/widget", "acme/gadget")


def test_a_point_in_time_lookup_never_matches_a_retired_record() -> None:
    rows = [_row("widget", retired_at=_MINTED + timedelta(days=1))]

    assert resolve_repository(CommitOrigin.of(None, "widget"), rows) is None
    assert resolve_repository(CommitOrigin.of(None, "widget"), [_row("widget")]) is not None
