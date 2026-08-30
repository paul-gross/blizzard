"""SQLAlchemy adapter for the runtime workspace-prompt override seam (package-private,
blizzard#410)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.workspace_prompts import IWriteWorkspacePromptRepository
from blizzard.runner.store.internal.base import RunnerStoreConnections
from blizzard.runner.store.schema import workspace_prompt

_log = get_logger("blizzard.runner.store")


class WorkspacePromptStore:
    """Read-write runtime workspace-prompt override adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def workspace_prompt_override(self, workspace_id: str) -> str | None:
        rows = self._store.all(select(workspace_prompt.c.prompt).where(workspace_prompt.c.workspace_id == workspace_id))
        return str(rows[0].prompt) if rows else None

    def set_workspace_prompt(self, workspace_id: str, *, prompt: str, at: datetime) -> None:
        with self._store.begin() as conn:
            existing = conn.execute(
                select(workspace_prompt.c.workspace_id).where(workspace_prompt.c.workspace_id == workspace_id)
            ).one_or_none()
            if existing is None:
                conn.execute(workspace_prompt.insert().values(workspace_id=workspace_id, prompt=prompt, updated_at=at))
            else:
                conn.execute(
                    workspace_prompt.update()
                    .where(workspace_prompt.c.workspace_id == workspace_id)
                    .values(prompt=prompt, updated_at=at)
                )
        _log.info("workspace prompt override set", workspace_id=workspace_id, length=len(prompt))

    def clear_workspace_prompt(self, workspace_id: str) -> bool:
        with self._store.begin() as conn:
            deleted = conn.execute(
                workspace_prompt.delete().where(workspace_prompt.c.workspace_id == workspace_id)
            ).rowcount
        _log.info("workspace prompt override cleared", workspace_id=workspace_id, existed=bool(deleted))
        return bool(deleted)


def _conforms_workspace_prompt_store(x: WorkspacePromptStore) -> IWriteWorkspacePromptRepository:
    return x
