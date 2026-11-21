"""The host's workspace registry — each workspace by id, with its provider, spawn root, standing prompt and pool
lock — and :class:`HostWorkspaces`, the one holder of the registry in force, which the host tick replaces whenever a
workspace record moves. A lease reaches its workspace through the id its ``env_bindings`` row names; a row naming
none, or one the host does not declare, resolves to no provider. A retired workspace serves only what it holds."""

from __future__ import annotations

import threading
from collections.abc import Generator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import KW_ONLY, dataclass
from typing import Protocol

from blizzard.runner.environments.provider import IWorkspaceProvider


def home_workspace_id(bound: Sequence[str], declared: Sequence[str]) -> str:
    """The workspace a runner is reported and addressed by as such: the first workspace its bindings name that the
    host declares, else the host's first; on a host declaring none, the first its bindings name, else ``""``."""
    fallback = declared[0] if declared else (bound[0] if bound else "")
    return next((workspace_id for workspace_id in bound if workspace_id in declared), fallback)


def held_workspace_id(held: str | None, bound: Sequence[str], declared: Sequence[str]) -> str:
    """The workspace a held ``env_bindings`` row is listed and claimed under: the one it names when the host declares
    it, else the runner's home (:func:`home_workspace_id`)."""
    return held if held is not None and held in declared else home_workspace_id(bound, declared)


class IWorkspaceSpawnRoots(Protocol):
    """Where a workspace's workers spawn, narrowed from :class:`IWorkspaces` (``bzh:seam-size-ceiling``)."""

    def spawn_root(self, workspace_id: str | None) -> str:
        """The directory the workspace's workers spawn in; ``""`` when each spawns in its own environment's workdir,
        or for no workspace or an undeclared one."""
        ...


class IWorkspaceSpawnSettings(IWorkspaceSpawnRoots, Protocol):
    """A spawn's reads of its workspace: where it spawns, and the standing prompt it carries."""

    def prompt(self, workspace_id: str | None) -> str:
        """The workspace's configured standing prompt, resolved once at build; ``""`` for no workspace or an
        undeclared one."""
        ...


class IWorkspaceProviders(Protocol):
    """Each workspace's provider, narrowed from :class:`IWorkspaces` (``bzh:seam-size-ceiling``)."""

    def provider(self, workspace_id: str | None) -> IWorkspaceProvider | None:
        """The workspace's provider, or ``None`` for no workspace or one the host does not declare."""
        ...


class IWorkspaceHomes(IWorkspaceProviders, Protocol):
    """A runner's home workspace, and its provider."""

    def home(self, bound: Sequence[str]) -> str:
        """:func:`home_workspace_id` over this host's declared workspaces."""
        ...


class IWorkspaceEnvironmentWorkdirs(Protocol):
    """Every workspace's listed environments, narrowed from :class:`IWorkspaces` (``bzh:seam-size-ceiling``)."""

    def environment_workdirs(self) -> dict[str, dict[str, str]]:
        """Each workspace's listed environments mapped to their workdirs, by workspace id."""
        ...


class IWorkspaceSessions(IWorkspaceSpawnSettings, IWorkspaceProviders, Protocol):
    """A worker session's reads of its workspace: where and with what prompt it spawns, and the provider its
    environments are read through."""


class IWorkspaceAcquisitionProviders(Protocol):
    """The providers a new acquisition may draw from."""

    def acquisition_provider(self, workspace_id: str | None) -> IWorkspaceProvider | None:
        """The workspace's provider while it is active; ``None`` for a retired workspace, an undeclared one or
        none — which serves no binding."""
        ...


class IWorkspaceAcquisition(IWorkspaceSessions, IWorkspaceAcquisitionProviders, Protocol):
    """A claim's reach into a workspace: the lock serialising acquisition from its pool and the workspace a held row is
    claimed under, beside the session reads of the spawn it starts."""

    def held(self, workspace_id: str | None, bound: Sequence[str]) -> str:
        """:func:`held_workspace_id` over this host's declared workspaces."""
        ...

    def try_acquisition_lock(self, workspace_id: str) -> AbstractContextManager[bool]:
        """The in-process lock serialising acquisition from the workspace's pool, taken without waiting: the context
        yields whether it was taken, and releases only a lock it took. Reentrant on the owning thread."""
        ...


class IWorkspaceEntries(Protocol):
    """The host's active workspaces as registered, in declaration order."""

    def entries(self) -> tuple[WorkspaceEntry, ...]: ...


class IWorkspaces(IWorkspaceAcquisition, IWorkspaceHomes, IWorkspaceEnvironmentWorkdirs, IWorkspaceEntries, Protocol):
    """The host's workspaces, each looked up by its id — every slice composed, for the bundles that hand them on."""


@dataclass(frozen=True)
class WorkspaceEntry:
    """What one workspace is registered with: its id, provider, spawn root and resolved standing prompt, its
    environment cap, and the record revision it was built at (``None`` for one built from no record)."""

    workspace_id: str
    provider: IWorkspaceProvider
    _: KW_ONLY
    spawn_root: str
    prompt: str = ""
    max_environments: int = 0
    revision: int | None = None


class WorkspaceLocks:
    """The host's acquisition locks, one per workspace id, made on first use and kept for the host's life — so every
    registry the host builds hands out the same lock for one workspace."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()

    def lock(self, workspace_id: str) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(workspace_id, threading.RLock())


class WorkspaceRegistry:
    """The :class:`IWorkspaces` over the host's active workspaces in declaration order, beside its ``retired`` ones,
    which serve only what they already hold. May be empty: a host with no workspace serves no binding. ``locks`` are
    the host's; a registry built without them holds its own, which no :class:`HostWorkspaces` accepts."""

    def __init__(
        self,
        entries: Sequence[WorkspaceEntry],
        *,
        retired: Sequence[WorkspaceEntry] = (),
        locks: WorkspaceLocks | None = None,
    ) -> None:
        self._workspaces: Mapping[str, WorkspaceEntry] = {entry.workspace_id: entry for entry in entries}
        self._retired: Mapping[str, WorkspaceEntry] = {
            entry.workspace_id: entry for entry in retired if entry.workspace_id not in self._workspaces
        }
        self.locks = locks if locks is not None else WorkspaceLocks()

    def ids(self) -> tuple[str, ...]:
        return tuple(self._workspaces)

    def entries(self) -> tuple[WorkspaceEntry, ...]:
        return tuple(self._workspaces.values())

    def entry(self, workspace_id: str) -> WorkspaceEntry | None:
        """The workspace's entry, active or retired; ``None`` for one the registry holds neither way."""
        return self._get(workspace_id)

    def retired_entries(self) -> tuple[WorkspaceEntry, ...]:
        return tuple(self._retired.values())

    def home(self, bound: Sequence[str]) -> str:
        return home_workspace_id(bound, self.ids())

    def held(self, workspace_id: str | None, bound: Sequence[str]) -> str:
        # A row held in a retired workspace stays under it until its claim releases it.
        if workspace_id is not None and workspace_id in self._retired:
            return workspace_id
        return held_workspace_id(workspace_id, bound, self.ids())

    def provider(self, workspace_id: str | None) -> IWorkspaceProvider | None:
        workspace = self._get(workspace_id)
        return workspace.provider if workspace is not None else None

    def acquisition_provider(self, workspace_id: str | None) -> IWorkspaceProvider | None:
        workspace = self._workspaces.get(workspace_id) if workspace_id is not None else None
        return workspace.provider if workspace is not None else None

    def spawn_root(self, workspace_id: str | None) -> str:
        workspace = self._get(workspace_id)
        return workspace.spawn_root if workspace is not None else ""

    def prompt(self, workspace_id: str | None) -> str:
        workspace = self._get(workspace_id)
        return workspace.prompt if workspace is not None else ""

    @contextmanager
    def try_acquisition_lock(self, workspace_id: str) -> Generator[bool, None, None]:
        lock = self.locks.lock(workspace_id)
        taken = lock.acquire(blocking=False)
        try:
            yield taken
        finally:
            if taken:
                lock.release()

    def environment_workdirs(self) -> dict[str, dict[str, str]]:
        return {
            workspace_id: workspace.provider.environment_workdirs()
            for workspace_id, workspace in {**self._retired, **self._workspaces}.items()
        }

    def _get(self, workspace_id: str | None) -> WorkspaceEntry | None:
        if workspace_id is None:
            return None
        return self._workspaces.get(workspace_id) or self._retired.get(workspace_id)


_workspace_registry_conforms: type[IWorkspaces] = WorkspaceRegistry


class HostWorkspaces:
    """The one holder of the host's current :class:`WorkspaceRegistry`, and itself an :class:`IWorkspaces`: each read
    goes to the registry in force at that read, so a registry set here reaches every reader with no rebuild of theirs.
    Every registry it is set to is built over its first one's acquisition locks (``bzh:environment-held-once``)."""

    def __init__(self, registry: WorkspaceRegistry | None = None) -> None:
        self._registry = registry if registry is not None else WorkspaceRegistry(())
        self.locks = self._registry.locks
        self._guard = threading.Lock()

    def current(self) -> WorkspaceRegistry:
        with self._guard:
            return self._registry

    def set(self, registry: WorkspaceRegistry) -> None:
        """Put ``registry`` in force; one built over other locks than the host's is refused."""
        if registry.locks is not self.locks:
            raise ValueError("a host's workspace registry is built over the host's acquisition locks")
        with self._guard:
            self._registry = registry

    def ids(self) -> tuple[str, ...]:
        return self.current().ids()

    def entries(self) -> tuple[WorkspaceEntry, ...]:
        return self.current().entries()

    def home(self, bound: Sequence[str]) -> str:
        return self.current().home(bound)

    def held(self, workspace_id: str | None, bound: Sequence[str]) -> str:
        return self.current().held(workspace_id, bound)

    def provider(self, workspace_id: str | None) -> IWorkspaceProvider | None:
        return self.current().provider(workspace_id)

    def acquisition_provider(self, workspace_id: str | None) -> IWorkspaceProvider | None:
        return self.current().acquisition_provider(workspace_id)

    def spawn_root(self, workspace_id: str | None) -> str:
        return self.current().spawn_root(workspace_id)

    def prompt(self, workspace_id: str | None) -> str:
        return self.current().prompt(workspace_id)

    def try_acquisition_lock(self, workspace_id: str) -> AbstractContextManager[bool]:
        return self.current().try_acquisition_lock(workspace_id)

    def environment_workdirs(self) -> dict[str, dict[str, str]]:
        return self.current().environment_workdirs()


_host_workspaces_conform: type[IWorkspaces] = HostWorkspaces
