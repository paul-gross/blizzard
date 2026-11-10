"""Whether a workspace still has an environment to hand out — a hint ahead of acquisition, never the decision.

Composed from the provider's standing pool and what the host's pool holds. A provider whose environments are minted on
acquire has no bounded pool and always counts as free. Acquisition stays authoritative under the host's pool lock."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from blizzard.runner.environments.provider import IWorkspaceProvider
from blizzard.runner.environments.repository import EnvBinding


def has_free_environment(pool: tuple[str, ...] | None, held: Sequence[EnvBinding]) -> bool:
    """Whether a known pool can hand out an environment; an empty pool mints on acquire."""
    if pool is None:
        return False
    if not pool:
        return True
    held_ids = {binding.environment_id for binding in held}
    return any(environment_id not in held_ids for environment_id in pool)


class IWorkspaceFreeEnvironment(Protocol):
    """A workspace's free-environment hint, narrowed for FILL (``bzh:narrow-seams``)."""

    def has_free_environment(self, workspace_id: str) -> bool:
        """Whether the workspace's pool holds an environment no chunk holds; ``True`` for an unbounded pool."""
        ...


class IWorkspaceProviderLookup(Protocol):
    def provider(self, workspace_id: str | None) -> IWorkspaceProvider | None: ...


class IPoolHeld(Protocol):
    def held_workspace_bindings(self, workspace_id: str) -> list[EnvBinding]: ...


class FreeEnvironments:
    """:class:`IWorkspaceFreeEnvironment` over the registry's providers and the pool-held read."""

    def __init__(self, providers: IWorkspaceProviderLookup, pool: IPoolHeld) -> None:
        self._providers = providers
        self._pool = pool

    def has_free_environment(self, workspace_id: str) -> bool:
        provider = self._providers.provider(workspace_id)
        pool = provider.pool() if provider is not None else None
        held = self._pool.held_workspace_bindings(workspace_id) if pool else []
        return has_free_environment(pool, held)
