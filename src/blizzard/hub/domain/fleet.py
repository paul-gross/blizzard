"""Fleet domain — the chunk locator.

:class:`Route` locates a chunk. Dependency-free domain objects (``bzh:domain-core``)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class Route:
    """The locator fact born complete at the claim.

    A chunk may hold several environments, each one ``environment_id`` under the same
    claim. ``route_id`` is ``None`` until the underlying row exists."""

    chunk_id: str
    runner_id: str
    workspace_id: str
    environment_ids: list[str]
    created_at: datetime
    route_id: str | None = None
