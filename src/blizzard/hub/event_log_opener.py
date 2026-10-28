"""The production opener of the hub's own event log for the offline store verbs (``bzh:dependency-injection``).

It is part of the short-lived process's composition root: it opens an engine on the resolved config's store, builds
the hub core over the root's clock, and disposes the engine when the caller is done.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager

from blizzard.foundation.clock import IClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.composition import build_hub_core
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.observability.hub_events import HubEventLogService


def system_event_log_opener(clock: IClock) -> Callable[[HubConfig], AbstractContextManager[HubEventLogService]]:
    """An opener of the hub event log over ``config``'s store, reading time from ``clock``."""

    @contextmanager
    def open_event_log(config: HubConfig) -> Generator[HubEventLogService, None, None]:
        engine = create_engine_from_url(config.db_url)
        try:
            yield build_hub_core(engine, clock=clock).hub_event_log
        finally:
            engine.dispose()

    return open_event_log
