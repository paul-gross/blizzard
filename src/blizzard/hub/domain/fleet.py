"""Fleet domain — the chunk locator and the fleet's lease view.

The :class:`Route` is what makes every chunk findable and reassignment thinkable;
the :class:`LeaseView` is the hub's copy of a runner-minted lease fact — the one
machine-local fact that travels, because the transition fence consumes its epoch.
Dependency-free domain objects (``bzh:domain-core``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Route:
    """The locator fact born complete at the claim.

    A chunk may hold several environments; each is one ``environment_id``
    under the same chunk/runner/workspace claim.
    """

    chunk_id: str
    runner_id: str
    workspace_id: str
    environment_ids: list[str]
    created_at: datetime


@dataclass(frozen=True)
class LeaseView:
    """The hub's view of a runner-minted lease — chunk, epoch, runner."""

    chunk_id: str
    runner_id: str
    epoch: int
    minted_at: datetime
