"""COMPLETE — give each held binding lacking them its workspace and its chunk's project, ahead of FILL."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.runner.environments.repository import (
    BindingCompletion,
    IWriteEnvironmentRepository,
    binding_completion,
)
from blizzard.runner.environments.workspaces import IWorkspaces
from blizzard.runner.hub.chunk_status_cache import IChunkViews
from blizzard.runner.hub.client import HubClientError


class HeldBindingCompletionStores(Protocol):
    @property
    def environments(self) -> IWriteEnvironmentRepository: ...


class HeldBindingCompletionContext(Protocol):
    @property
    def stores(self) -> HeldBindingCompletionStores: ...
    @property
    def workspaces(self) -> IWorkspaces: ...
    @property
    def chunk_views(self) -> IChunkViews: ...


@dataclass(frozen=True)
class HeldBindingCompletion:
    """A row takes the workspace whose provider lists its workdir, and its project from the chunk-status batch read;
    one the hub cannot answer, or matching no workspace, keeps its gap and its lease until a later tick. Each
    completion fills only an empty column in one transaction, so a crash re-runs it harmlessly."""

    ctx: HeldBindingCompletionContext

    def complete(self) -> None:
        ctx = self.ctx
        rows = ctx.stores.environments.incomplete_held_bindings()
        if not rows:
            return
        listed = ctx.workspaces.environment_workdirs()
        lacking_project = {row.chunk_id for row in rows if row.project_id is None}
        ctx.chunk_views.prime(lacking_project)
        projects = {chunk_id: self._project(chunk_id) for chunk_id in lacking_project}
        completions: list[BindingCompletion] = []
        for row in rows:
            completion = binding_completion(row, listed_workdirs=listed, project_id=projects.get(row.chunk_id))
            if completion is not None:
                completions.append(completion)
        ctx.stores.environments.complete_bindings(completions)

    def _project(self, chunk_id: str) -> str | None:
        """The chunk's project from the tick's primed batch read; ``None`` when the hub cannot say yet."""
        try:
            return self.ctx.chunk_views.get(chunk_id).project_id
        except HubClientError:  # covers ChunkNotFoundError: an unknown chunk names no project
            return None
