"""Scope domain objects and services (unit tier, blizzard#389): ``ScopeSlug.parse``'s
validation, ``ScopeRegistry``'s mint-on-name and edit, and ``ScopeLifecycle``'s
retire/enable brake — each isolated from a store behind a fake repository
(``bzh:domain-core``, the ``tests/test_graph_lifecycle_service.py`` shape)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.scopes import (
    IWriteScopeRepository,
    Scope,
    ScopeLifecycle,
    ScopeRegistry,
    ScopeSlug,
    ScopeSlugError,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("raw", ["Blizzard", "blizzard_ops", "blizzard ops", "blizzard!", "  "])
def test_parse_rejects_a_slug_outside_the_pattern_naming_it(raw: str) -> None:
    with pytest.raises(ScopeSlugError) as exc_info:
        ScopeSlug.parse(raw)
    assert raw in str(exc_info.value)


def test_parse_rejects_an_empty_slug() -> None:
    with pytest.raises(ScopeSlugError):
        ScopeSlug.parse("")


def test_parse_accepts_lowercase_alnum_and_hyphen() -> None:
    assert ScopeSlug.parse("blizzard-ops-2").value == "blizzard-ops-2"


@dataclass
class _FakeScopeRepo:
    """Only the seams ``ScopeRegistry``/``ScopeLifecycle`` use are live; anything else
    is a bug (``bzh:domain-core`` — no store, no tokens)."""

    ensured: list[tuple[str, str, datetime]] = field(default_factory=list)
    edited: list[tuple[str, str]] = field(default_factory=list)
    recorded: list[tuple[str, bool, str, datetime]] = field(default_factory=list)
    ensure_returns: Scope | None = None
    edit_returns: Scope | None = None

    def ensure(self, slug: str, *, description: str, at: datetime) -> Scope:
        self.ensured.append((slug, description, at))
        return self.ensure_returns or Scope(slug=slug, description=description, created_at=at)

    def edit_description(self, slug: str, *, description: str) -> Scope:
        self.edited.append((slug, description))
        return self.edit_returns or Scope(slug=slug, description=description, created_at=_T0)

    def record_lifecycle(self, slug: str, *, retired: bool, at: datetime, by: str) -> None:
        self.recorded.append((slug, retired, by, at))

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


def _as_write_repo(repo: _FakeScopeRepo) -> IWriteScopeRepository:
    return cast(IWriteScopeRepository, repo)


def test_registry_ensure_delegates_to_the_repo_with_the_clock_instant() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeScopeRepo()
    registry = ScopeRegistry(scopes=_as_write_repo(repo), clock=clock)

    registry.ensure(ScopeSlug.parse("blizzard"), description="the repo")

    assert repo.ensured == [("blizzard", "the repo", _T0)]


def test_registry_ensure_defaults_description_to_empty() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeScopeRepo()
    registry = ScopeRegistry(scopes=_as_write_repo(repo), clock=clock)

    registry.ensure(ScopeSlug.parse("blizzard"))

    assert repo.ensured == [("blizzard", "", _T0)]


def test_registry_edit_changes_only_the_description() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeScopeRepo()
    registry = ScopeRegistry(scopes=_as_write_repo(repo), clock=clock)
    scope = Scope(slug="blizzard", description="old", created_at=_T0)

    registry.edit(scope, description="new")

    assert repo.edited == [("blizzard", "new")]


def test_lifecycle_retire_records_a_retired_true_fact() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeScopeRepo()
    lifecycle = ScopeLifecycle(scopes=_as_write_repo(repo), clock=clock)
    scope = Scope(slug="blizzard", description="", created_at=_T0)

    lifecycle.retire(scope, by="operator")

    assert repo.recorded == [("blizzard", True, "operator", _T0)]


def test_lifecycle_enable_records_a_retired_false_fact() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeScopeRepo()
    lifecycle = ScopeLifecycle(scopes=_as_write_repo(repo), clock=clock)
    scope = Scope(slug="blizzard", description="", created_at=_T0)

    lifecycle.enable(scope, by="operator")

    assert repo.recorded == [("blizzard", False, "operator", _T0)]


def test_lifecycle_retire_twice_is_a_harmless_no_op() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeScopeRepo()
    lifecycle = ScopeLifecycle(scopes=_as_write_repo(repo), clock=clock)
    scope = Scope(slug="blizzard", description="", created_at=_T0)

    lifecycle.retire(scope, by="operator")
    lifecycle.retire(scope, by="operator")

    assert repo.recorded == [("blizzard", True, "operator", _T0), ("blizzard", True, "operator", _T0)]
