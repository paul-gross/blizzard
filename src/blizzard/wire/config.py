"""The configuration change log as served — ``GET /api/config/changes``."""

from __future__ import annotations

from pydantic import BaseModel


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
