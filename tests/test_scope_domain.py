"""Scope domain objects and services (unit tier): ``ScopeSlug.parse``'s
validation, the ``Scope`` record's sparse edit, revision, and change decisions,
``ScopeRegistry``'s mint-on-name and edit, and ``ScopeLifecycle``'s retire/enable brake — each isolated from a store behind a fake repository
(``bzh:domain-core``, the ``tests/test_graph_lifecycle_service.py`` shape)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, ConfigChange, Door, FieldChange, RecordKind
from blizzard.hub.domain.config.work_sources import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.garden.scopes import (
    IWriteScopeRepository,
    Scope,
    ScopeEdit,
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


_CTX = ChangeContext(actor="usr_1", door=Door.CLI)


@dataclass
class _FakeScopeRepo:
    """Only the seams ``ScopeRegistry``/``ScopeLifecycle`` use are live; anything else
    is a bug (``bzh:domain-core`` — no store, no tokens)."""

    stored: dict[str, Scope] = field(default_factory=dict)
    ensured: list[tuple[Scope, ConfigChange]] = field(default_factory=list)
    updated: list[tuple[Scope, int, ConfigChange]] = field(default_factory=list)
    recorded: list[tuple[Scope, bool, int, str, ConfigChange]] = field(default_factory=list)

    def get(self, slug: str) -> Scope | None:
        return self.stored.get(slug)

    def ensure(self, record: Scope, *, change: ConfigChange) -> Scope:
        self.ensured.append((record, change))
        return record

    def update(self, record: Scope, *, from_revision: int, change: ConfigChange) -> Scope:
        self.updated.append((record, from_revision, change))
        return record

    def record_lifecycle(
        self, record: Scope, *, retired: bool, from_revision: int, at: datetime, by: str, change: ConfigChange
    ) -> Scope:
        self.recorded.append((record, retired, from_revision, by, change))
        return record

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


def _as_write_repo(repo: _FakeScopeRepo) -> IWriteScopeRepository:
    return cast(IWriteScopeRepository, repo)


def _scope(*, description: str = "old", revision: int = 1, retired: bool = False) -> Scope:
    return Scope(slug="blizzard", description=description, created_at=_T0, revision=revision, retired=retired)


# --- The model's verbs --------------------------------------------------------


def test_new_is_revision_one_with_a_create_change_keyed_on_the_slug() -> None:
    record, change = Scope.new(ScopeSlug.parse("blizzard"), "the repo", _CTX, at=_T0)

    assert (record.slug, record.description, record.revision, record.retired) == ("blizzard", "the repo", 1, False)
    assert (change.record_kind, change.record_key, change.revision, change.op) == (
        RecordKind.SCOPE,
        "blizzard",
        1,
        ChangeOp.CREATE,
    )
    assert change.diff == (FieldChange("description", None, "the repo"),)
    assert (change.actor, change.door) == ("usr_1", Door.CLI)


def test_edit_sets_a_present_description_and_moves_the_revision() -> None:
    decided = _scope(revision=3).edit(ScopeEdit(description="new"), _CTX, if_match=None, at=_T0)

    assert decided is not None
    edited, change = decided
    assert (edited.description, edited.revision) == ("new", 4)
    assert (change.op, change.revision, change.diff) == (ChangeOp.EDIT, 4, (FieldChange("description", "old", "new"),))


@pytest.mark.parametrize("edit", [ScopeEdit(), ScopeEdit(description="old")])
def test_edit_that_changes_nothing_decides_nothing(edit: ScopeEdit) -> None:
    assert _scope().edit(edit, _CTX, if_match=None, at=_T0) is None


def test_edit_refuses_a_null_description_naming_the_field() -> None:
    with pytest.raises(ConfigFieldError) as exc_info:
        _scope().edit(ScopeEdit(description=None), _CTX, if_match=None, at=_T0)
    assert exc_info.value.field == "description"


def test_a_stale_if_match_refuses_even_an_edit_that_changes_nothing() -> None:
    with pytest.raises(ConfigRevisionConflict) as exc_info:
        _scope(revision=2).edit(ScopeEdit(), _CTX, if_match=1, at=_T0)
    assert exc_info.value.current == 2


def test_set_retired_flips_and_moves_the_revision() -> None:
    decided = _scope(revision=2).set_retired(True, _CTX, if_match=2, at=_T0)

    assert decided is not None
    moved, change = decided
    assert (moved.retired, moved.revision) == (True, 3)
    assert (change.op, change.diff) == (ChangeOp.RETIRE, (FieldChange("retired", False, True),))


@pytest.mark.parametrize("retired", [True, False])
def test_set_retired_to_where_it_stands_decides_nothing(retired: bool) -> None:
    assert _scope(retired=retired).set_retired(retired, _CTX, if_match=None, at=_T0) is None


# --- The services -------------------------------------------------------------


def test_registry_ensure_mints_an_unseen_slug_with_its_create_change() -> None:
    repo = _FakeScopeRepo()
    registry = ScopeRegistry(scopes=_as_write_repo(repo), clock=FixedClock(instant=_T0))

    registry.ensure(ScopeSlug.parse("blizzard"), _CTX, description="the repo")

    [(record, change)] = repo.ensured
    assert (record.slug, record.description, record.created_at) == ("blizzard", "the repo", _T0)
    assert change.op is ChangeOp.CREATE


def test_registry_ensure_defaults_description_to_empty() -> None:
    repo = _FakeScopeRepo()
    registry = ScopeRegistry(scopes=_as_write_repo(repo), clock=FixedClock(instant=_T0))

    registry.ensure(ScopeSlug.parse("blizzard"), _CTX)

    assert repo.ensured[0][0].description == ""


def test_registry_ensure_returns_a_stored_scope_and_writes_nothing() -> None:
    stored = _scope()
    repo = _FakeScopeRepo(stored={"blizzard": stored})
    registry = ScopeRegistry(scopes=_as_write_repo(repo), clock=FixedClock(instant=_T0))

    assert registry.ensure(ScopeSlug.parse("blizzard"), _CTX, description="other") is stored
    assert repo.ensured == []


def test_registry_resolve_hands_back_an_unseen_slugs_mint_without_writing() -> None:
    repo = _FakeScopeRepo()
    registry = ScopeRegistry(scopes=_as_write_repo(repo), clock=FixedClock(instant=_T0))

    scope, mint = registry.resolve(ScopeSlug.parse("blizzard"), _CTX)

    assert mint is not None and mint[0] == scope
    assert repo.ensured == []


def test_registry_edit_compare_and_sets_from_the_read_revision() -> None:
    repo = _FakeScopeRepo()
    registry = ScopeRegistry(scopes=_as_write_repo(repo), clock=FixedClock(instant=_T0))

    registry.edit(_scope(revision=2), ScopeEdit(description="new"), _CTX)

    [(record, from_revision, change)] = repo.updated
    assert (record.description, record.revision, from_revision, change.revision) == ("new", 3, 2, 3)


def test_registry_edit_that_changes_nothing_writes_nothing() -> None:
    repo = _FakeScopeRepo()
    registry = ScopeRegistry(scopes=_as_write_repo(repo), clock=FixedClock(instant=_T0))

    registry.edit(_scope(), ScopeEdit(description="old"), _CTX)

    assert repo.updated == []


def test_lifecycle_retire_records_the_fact_by_and_the_change_actor() -> None:
    repo = _FakeScopeRepo()
    lifecycle = ScopeLifecycle(scopes=_as_write_repo(repo), clock=FixedClock(instant=_T0))

    lifecycle.retire(_scope(), _CTX, by="operator")

    [(record, retired, from_revision, by, change)] = repo.recorded
    assert (record.retired, retired, from_revision, by) == (True, True, 1, "operator")
    assert (change.actor, change.op) == ("usr_1", ChangeOp.RETIRE)


def test_lifecycle_enable_records_a_retired_false_fact() -> None:
    repo = _FakeScopeRepo()
    lifecycle = ScopeLifecycle(scopes=_as_write_repo(repo), clock=FixedClock(instant=_T0))

    lifecycle.enable(_scope(retired=True), _CTX, by="operator")

    assert [(r.retired, retired) for r, retired, *_ in repo.recorded] == [(False, False)]


def test_lifecycle_retire_of_a_retired_scope_writes_nothing() -> None:
    repo = _FakeScopeRepo()
    lifecycle = ScopeLifecycle(scopes=_as_write_repo(repo), clock=FixedClock(instant=_T0))

    lifecycle.retire(_scope(retired=True), _CTX, by="operator")

    assert repo.recorded == []
