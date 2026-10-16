"""The host's workspace registry: every workspace the host declares, by id.

Each workspace has its own provider, the spawn root its workers start in, its resolved standing prompt, and the lock
that serialises acquisition from its pool. A lease reaches its workspace through the id its ``env_bindings`` row
names, never through a runner-wide workspace; a row naming none, or one the host does not declare, resolves to no
provider, an empty spawn root and no prompt."""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol

from blizzard.runner.environments.provider import IWorkspaceProvider


def home_workspace_id(bound: Sequence[str], declared: Sequence[str]) -> str:
    """The workspace a runner is reported and addressed by as such: the first workspace its bindings name that the
    host declares, else the host's first. ``declared`` is never empty."""
    return next((workspace_id for workspace_id in bound if workspace_id in declared), declared[0])


def held_workspace_id(held: str | None, bound: Sequence[str], declared: Sequence[str]) -> str:
    """The workspace a held ``env_bindings`` row is listed and claimed under: the one it names when the host declares
    it, else the runner's home (:func:`home_workspace_id`)."""
    return held if held is not None and held in declared else home_workspace_id(bound, declared)


class IWorkspaces(Protocol):
    """The host's workspaces, each looked up by its id."""

    def ids(self) -> tuple[str, ...]:
        """Every declared workspace id, in declaration order — never empty."""
        ...

    def home(self, bound: Sequence[str]) -> str:
        """:func:`home_workspace_id` over this host's declared workspaces."""
        ...

    def held(self, workspace_id: str | None, bound: Sequence[str]) -> str:
        """:func:`held_workspace_id` over this host's declared workspaces."""
        ...

    def provider(self, workspace_id: str | None) -> IWorkspaceProvider | None:
        """The workspace's provider, or ``None`` for no workspace or one the host does not declare."""
        ...

    def spawn_root(self, workspace_id: str | None) -> str:
        """The directory the workspace's workers spawn in; ``""`` when each spawns in its own environment's workdir,
        or for no workspace or an undeclared one."""
        ...

    def prompt(self, workspace_id: str | None) -> str:
        """The workspace's configured standing prompt, resolved once at build; ``""`` for no workspace or an
        undeclared one."""
        ...

    def try_acquisition_lock(self, workspace_id: str) -> AbstractContextManager[bool]:
        """The in-process lock serialising acquisition from the workspace's pool, taken without waiting: the context
        yields whether it was taken, and releases only a lock it took. Reentrant on the owning thread."""
        ...

    def environment_workdirs(self) -> dict[str, dict[str, str]]:
        """Each workspace's listed environments mapped to their workdirs, by workspace id."""
        ...


class _Workspace:
    """One registered workspace — the registry's own working state."""

    def __init__(self, provider: IWorkspaceProvider, *, spawn_root: str, prompt: str) -> None:
        self.provider = provider
        self.spawn_root = spawn_root
        self.prompt = prompt
        self.lock = threading.RLock()


class WorkspaceEntry:
    """What one workspace is registered with: its id, provider, spawn root and resolved standing prompt."""

    def __init__(self, workspace_id: str, provider: IWorkspaceProvider, *, spawn_root: str, prompt: str = "") -> None:
        self.workspace_id = workspace_id
        self.provider = provider
        self.spawn_root = spawn_root
        self.prompt = prompt


class WorkspaceRegistry:
    """The :class:`IWorkspaces` a host builds once, over its declared workspaces in declaration order."""

    def __init__(self, entries: Sequence[WorkspaceEntry]) -> None:
        if not entries:
            raise ValueError("a workspace registry needs at least one workspace")
        self._workspaces: Mapping[str, _Workspace] = {
            entry.workspace_id: _Workspace(entry.provider, spawn_root=entry.spawn_root, prompt=entry.prompt)
            for entry in entries
        }

    def ids(self) -> tuple[str, ...]:
        return tuple(self._workspaces)

    def home(self, bound: Sequence[str]) -> str:
        return home_workspace_id(bound, self.ids())

    def held(self, workspace_id: str | None, bound: Sequence[str]) -> str:
        return held_workspace_id(workspace_id, bound, self.ids())

    def provider(self, workspace_id: str | None) -> IWorkspaceProvider | None:
        workspace = self._get(workspace_id)
        return workspace.provider if workspace is not None else None

    def spawn_root(self, workspace_id: str | None) -> str:
        workspace = self._get(workspace_id)
        return workspace.spawn_root if workspace is not None else ""

    def prompt(self, workspace_id: str | None) -> str:
        workspace = self._get(workspace_id)
        return workspace.prompt if workspace is not None else ""

    @contextmanager
    def try_acquisition_lock(self, workspace_id: str) -> Iterator[bool]:
        lock = self._workspaces[workspace_id].lock
        taken = lock.acquire(blocking=False)
        try:
            yield taken
        finally:
            if taken:
                lock.release()

    def environment_workdirs(self) -> dict[str, dict[str, str]]:
        return {
            workspace_id: workspace.provider.environment_workdirs()
            for workspace_id, workspace in self._workspaces.items()
        }

    def _get(self, workspace_id: str | None) -> _Workspace | None:
        return self._workspaces.get(workspace_id) if workspace_id is not None else None


def _conforms_workspace_registry(x: WorkspaceRegistry) -> IWorkspaces:
    return x
