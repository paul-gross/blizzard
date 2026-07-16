"""The PM source registry (D-105) — configured sources looked up by name.

A plain, dependency-free ``dict`` wrapper (``bzh:domain-core``, no I/O): the
credentialed clients live behind each entry's adapter, built at the composition root
(:mod:`blizzard.hub.pm.internal.factory`). An empty registry is a legal hub with no PM
reach — the pass-through routes degrade per-chunk/per-pointer rather than refusing to
start (D-105).

:func:`resolve_source` is the D-106 repo-matching resolver: the pointer carries no
source name yet (D-104 lands in Phase 3), so finding a pointer's binding means asking
every configured source whether it owns the pointer's repo. Both the ingest-time 422
rule and the board label/fetch read use this one resolver — built once, per Phase 1's
carried-forward finding that a first-entry shim renders a lying label the moment more
than one source is configured.
"""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.hub.domain.work import PmPointer
from blizzard.hub.pm.source import IPmSource, IPmSourceRegistry


class PmSourceRegistry:
    """The hub's configured PM sources, keyed by their declared ``name``."""

    def __init__(self, sources: Mapping[str, IPmSource] | None = None) -> None:
        self._sources = dict(sources or {})

    def get(self, name: str) -> IPmSource | None:
        return self._sources.get(name)

    def names(self) -> list[str]:
        return list(self._sources.keys())


def _conforms_pm_source_registry(x: PmSourceRegistry) -> IPmSourceRegistry:
    return x


def resolve_source(registry: IPmSourceRegistry, pointer: PmPointer) -> IPmSource | None:
    """The configured source whose repo matches ``pointer`` (D-106), or ``None``.

    Tries every configured source's :meth:`~blizzard.hub.pm.source.IPmSource.owns` in
    turn; the first (and, by config-load's duplicate-``(provider, repo)`` rejection,
    only) match wins. ``None`` when no configured source claims it — the caller decides
    what that means (a 422 at ingest, a null label at read)."""
    for name in registry.names():
        source = registry.get(name)
        if source is not None and source.owns(pointer):
            return source
    return None
