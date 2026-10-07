"""Scope and routine entries of a configuration document (``bzh:config-apply``).

The apply planner reconciles these through the record Protocol ``config`` owns; each entry carries the
garden rules a document write is held to, with one departure from the verbs: an apply never mints a scope
implicitly, so a routine entry may only name a scope that is stored or declared in the same document."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.apply import DeclarableRecord, DeclaredReferences
from blizzard.hub.domain.config.changes import ChangeContext, ConfigChange
from blizzard.hub.domain.config.work_sources import ConfigFieldError
from blizzard.hub.domain.garden.routines import (
    SCOPES_FIELD,
    Routine,
    RoutineEdit,
    RoutineGraphUnresolvedError,
    require_graph_change_resolves,
)
from blizzard.hub.domain.garden.scopes import Scope, ScopeEdit, ScopeSlug, ScopeSlugError
from blizzard.hub.domain.graph.harnesses import InvalidHarnesses, validated_harnesses


@domain_model
@dataclass(frozen=True)
class ScopeDeclaration:
    """A scope the document names by slug: its create description, and the sparse edit of what it states."""

    name: str
    description: str
    edit: ScopeEdit

    def check(self, refs: DeclaredReferences, stored: DeclarableRecord | None) -> None:
        try:
            ScopeSlug.parse(self.name)
        except ScopeSlugError as exc:
            raise ConfigFieldError("slug", str(exc)) from exc

    def create(self, ctx: ChangeContext, *, at: datetime) -> tuple[Scope, ConfigChange]:
        return Scope.new(ScopeSlug.parse(self.name), self.description, ctx, at=at)

    def settle(self, record: DeclarableRecord, refs: DeclaredReferences, ctx: ChangeContext, *, at: datetime) -> None:
        return None


@domain_model
@dataclass(frozen=True)
class RoutineDeclaration:
    """A routine the document names: its create fields, the sparse edit of what it states, and the
    linked scope set it states (``None`` leaves the stored set). ``routine_id`` is used only by a create."""

    name: str
    routine_id: str
    graph_name: str
    default_scope_slug: str
    default_model: Sequence[str]
    default_effort: str | None
    default_harnesses: Sequence[str]
    edit: RoutineEdit
    scopes: Sequence[str] | None = None

    def check(self, refs: DeclaredReferences, stored: DeclarableRecord | None) -> None:
        """The graph resolves to an enabled one when the entry creates the routine or moves it to another
        graph, and every scope it names is stored or declared; the harness preference is well-formed."""
        try:
            validated_harnesses(list(self.default_harnesses))
        except InvalidHarnesses as exc:
            raise ConfigFieldError("default_harnesses", str(exc)) from exc
        try:
            require_graph_change_resolves(
                stored.graph_name if isinstance(stored, Routine) else None,
                self.graph_name,
                lambda name: name in refs.enabled_graphs,
            )
        except RoutineGraphUnresolvedError as exc:
            raise ConfigFieldError("graph_name", str(exc)) from exc
        if self.default_scope_slug not in refs.scopes:
            raise ConfigFieldError("default_scope_slug", _unknown_scope(self.default_scope_slug))
        for slug in self.scopes or ():
            if slug not in refs.scopes:
                raise ConfigFieldError(SCOPES_FIELD, _unknown_scope(slug))

    def create(self, ctx: ChangeContext, *, at: datetime) -> tuple[Routine, ConfigChange]:
        return Routine.new(
            routine_id=self.routine_id,
            name=self.name,
            graph_name=self.graph_name,
            default_scope_slug=self.default_scope_slug,
            default_model=self.default_model,
            default_effort=self.default_effort,
            default_harnesses=self.default_harnesses,
            ctx=ctx,
            at=at,
        )

    def settle(
        self, record: DeclarableRecord, refs: DeclaredReferences, ctx: ChangeContext, *, at: datetime
    ) -> tuple[Routine, ConfigChange] | None:
        """Move the linked set to the stated one; the routine's default scope always stays a member."""
        assert isinstance(record, Routine)
        if self.scopes is None:
            return None
        linked = {*refs.linked_scopes.get(self.name, ()), record.default_scope_slug}
        stated = {*self.scopes, record.default_scope_slug}
        return record.with_scopes(sorted(linked), sorted(stated), ctx, if_match=None, at=at)


def _unknown_scope(slug: str) -> str:
    return f"scope {slug!r} is neither stored nor declared in this document"
