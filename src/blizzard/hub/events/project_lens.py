"""The project lens over the hub's runner-naming rows and frames: which runners serve a project.

A row or frame naming a chunk belongs to that chunk's project. One naming only a runner belongs to every project
that runner serves, read off its served declaration (``blizzard-product:/plans/projects/hub/spec/surfaces.md``
§API). The lens selects; it is no access boundary, since a tenant member reads every project."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from blizzard.foundation.events.broker import Event
from blizzard.foundation.hub_event_types import HubEventType
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry


def runners_serving(registry: IReadRunnerRegistry, project: ProjectId) -> frozenset[str]:
    """The ids of the tenant's runners, retired ones included, whose served declaration resolves to ``project`` —
    one registry read."""
    return frozenset(
        runner.runner_id for runner in registry.list_runners(include_retired=True) if runner.serves(project)
    )


#: The ``runner-changed`` kinds that can change which projects a runner serves: an add names them, a registration
#: declares them.
_SERVED_CHANGING: frozenset[str] = frozenset({"added", "registered"})


class ProjectLens:
    """Which of a tenant's stream frames a subscriber filtering by one project is sent. A frame stamped with a
    ``project_id`` is sent when it names this project; a ``runner-changed`` frame, or an ``event-logged`` frame naming
    a runner and no chunk, when that runner serves it; every other frame — ``queue-changed``, a frame naming neither
    a chunk nor a runner, one whose chunk is gone — is sent. The served set is held, and read again through
    ``refresh`` only on a ``runner-changed`` frame that can change it, so the stream never reads a store per frame."""

    def __init__(self, project: str, runners: frozenset[str], refresh: Callable[[], Awaitable[frozenset[str]]]) -> None:
        self._project = project
        self._runners = runners
        self._refresh = refresh

    async def admits(self, event: Event) -> bool:
        """Whether ``event`` is sent to this subscriber."""
        payload = json.loads(event.data)
        project_id = payload.get("project_id")
        if project_id is not None:
            return project_id == self._project
        runner_id = payload.get("runner_id")
        if event.type == HubEventType.RUNNER_CHANGED:
            if payload.get("kind") in _SERVED_CHANGING:
                self._runners = await self._refresh()
            return runner_id in self._runners
        if event.type == HubEventType.EVENT_LOGGED and payload.get("chunk_id") is None and runner_id is not None:
            return runner_id in self._runners
        return True
