"""The production opener of the hub's own event log for the offline store verbs (``bzh:dependency-injection``).

It is part of the short-lived process's composition root: it opens an engine on the resolved config's store, builds
the hub core over the root's clock, and closes the core's container, then disposes the engine, when the caller is done.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, closing, contextmanager

from blizzard.foundation.clock import IClock
from blizzard.hub.composition import build_hub_core, open_hub_store
from blizzard.hub.config import HubConfig
from blizzard.hub.delivery.action_registry import build_hub_actions
from blizzard.hub.domain.observability.hub_events import HubEventLogService


def system_event_log_opener(clock: IClock) -> Callable[[HubConfig], AbstractContextManager[HubEventLogService]]:
    """An opener of the hub event log over ``config``'s store, reading time from ``clock``."""

    @contextmanager
    def open_event_log(config: HubConfig) -> Generator[HubEventLogService, None, None]:
        engine, connections = open_hub_store(config.db_url)
        try:
            core = build_hub_core(connections, clock=clock, actions=build_hub_actions(os.environ))
            with closing(core.process):
                yield core.hub_event_log
        finally:
            engine.dispose()

    return open_event_log
