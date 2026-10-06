"""The configuration surface as served — the change log (``GET /api/config/changes``) and the
declarative document (``POST /api/config/apply``, ``GET /api/config/export``)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from blizzard.wire.repository import RepositoryDocument
from blizzard.wire.routine import RoutineDocument
from blizzard.wire.scope import ScopeDocument
from blizzard.wire.work_source import WorkSourceDocument


class FieldChangeView(BaseModel):
    """One changed field. A secret's value never appears in a diff."""

    field: str
    old: object = None
    new: object = None


class ConfigChangeView(BaseModel):
    """One row of the change log."""

    id: int
    recorded_at: str
    actor: str
    door: str
    record_kind: str
    record_key: str
    revision: int
    op: str
    diff: list[FieldChangeView]
    apply_id: str | None = None


class ConfigChangesPage(BaseModel):
    """A newest-first page; ``next_before`` is the ``before`` that fetches the next, ``None`` at the end."""

    changes: list[ConfigChangeView]
    next_before: int | None = None


class ConfigDocument(BaseModel):
    """A declarative configuration document. Each entry is the kind's own document model, so a field an
    entry omits is left as stored; scopes reconcile before routines, so a routine may name a scope the
    same document declares; ``secrets`` lists secret names that must already be active.
    ``GET /api/config/export`` writes every field of every entry."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    secrets: list[str] = []
    work_sources: list[WorkSourceDocument] = []
    repositories: list[RepositoryDocument] = []
    scopes: list[ScopeDocument] = []
    routines: list[RoutineDocument] = []


class ConfigApplyOutcome(BaseModel):
    """One outcome of an apply. ``op`` is ``create``, ``edit`` or ``enable`` for a change written and
    ``unchanged`` for a named record that needed none; ``diff`` is empty for ``unchanged``."""

    kind: str
    key: str
    op: str
    diff: list[FieldChangeView] = []


class ConfigApplyResponse(BaseModel):
    """What an apply did, or — under ``dry_run`` — would do, with the identical ``outcomes``.
    ``apply_id`` groups the change rows a real apply wrote; ``None`` for a dry run."""

    dry_run: bool
    apply_id: str | None = None
    outcomes: list[ConfigApplyOutcome]
