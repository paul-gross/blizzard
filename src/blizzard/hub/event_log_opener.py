"""The production opener of the hub's own event log for the offline store verbs (``bzh:dependency-injection``).

It is part of the short-lived process's composition root: it opens an engine on the resolved config's store, opens the
store-backed hub root over the caller's clock, and yields the root's registered event-log service. It closes the root,
then disposes the engine, when the caller is done — or when the root fails to open.
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Mapping
from contextlib import AbstractContextManager, closing, contextmanager

from blizzard.foundation.clock import IClock
from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing
from blizzard.hub.composition import open_hub_store
from blizzard.hub.config import HubConfig
from blizzard.hub.delivery.action_registry import build_hub_actions
from blizzard.hub.domain.observability.hub_events import HubEventLogService
from blizzard.hub.process_root import open_store_backed


def system_event_log_opener(
    clock: IClock, environ: Mapping[str, str]
) -> Callable[[HubConfig], AbstractContextManager[HubEventLogService]]:
    """An opener of the hub event log over ``config``'s store, reading time from ``clock`` and ``environ``."""

    @contextmanager
    def open_event_log(config: HubConfig) -> Generator[HubEventLogService, None, None]:
        engine, connections = open_hub_store(config.db_url)
        try:
            process = open_store_backed(
                connections,
                clock=clock,
                config=config,
                platform_tracing=DisabledPlatformTracing(),
                environ=environ,
                actions=build_hub_actions(environ),
            )
            with closing(process):
                yield process.resolver.get(HubEventLogService)
        finally:
            engine.dispose()

    return open_event_log
