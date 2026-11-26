"""A store-backed work-source registry over a hub harness, and the statements its reads issue."""

from __future__ import annotations

import re
from collections.abc import Callable

from blizzard.hub.domain.config.work_sources import ConfiguredWorkSource, IReadWorkSourceRepository
from blizzard.hub.live_config import ConfigObjectCache
from blizzard.hub.store.internal.config_revisions_store import ConfigRevisionsStore
from blizzard.hub.work_sources.internal.hub_work_source import HubWorkSource
from blizzard.hub.work_sources.registry import BuiltWorkSource, StoreWorkSourceRegistry
from tests.support.scaffolding import HubHarness, capture_statements, hub_store_connections

_REGISTRY_TABLES = re.compile(r"\b(?:FROM|JOIN)\s+(?:work_sources|work_source_lifecycle_facts)\b")


def stored_registry(
    hub: HubHarness, build: Callable[[ConfiguredWorkSource], BuiltWorkSource]
) -> StoreWorkSourceRegistry:
    """The production registry over ``hub``'s own records — the harness's ``work_sources`` fake issues no
    statements — with a cache of its own."""
    built_in = hub.work_sources.get("hub")
    assert isinstance(built_in, HubWorkSource)
    return StoreWorkSourceRegistry(
        records=hub.default_adapter(IReadWorkSourceRepository),
        objects=ConfigObjectCache(ConfigRevisionsStore(hub_store_connections(hub.engine))),
        build=build,
        built_in=built_in,
    )


def registry_statements(hub: HubHarness, fn: Callable[[], object]) -> int:
    """How many of ``fn``'s statements read the config revisions or work-source records."""
    with capture_statements(hub.engine) as statements:
        fn()
    return sum(_REGISTRY_TABLES.search(statement) is not None for statement, _ in statements)
