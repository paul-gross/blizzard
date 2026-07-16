"""The PM source registry (D-106) — configured sources looked up by name.

A plain, dependency-free ``dict`` wrapper (``bzh:domain-core``, no I/O): the
credentialed clients live behind each entry's adapter, built at the composition root
(:mod:`blizzard.hub.pm.internal.factory`). An empty registry is a legal hub with no PM
reach — the pass-through routes degrade per-chunk/per-pointer rather than refusing to
start (D-106).

D-105 gives the pointer its own ``source`` name, so finding a pointer's binding is a
plain lookup — ``registry.get(pointer.source)`` — rather than the D-107 repo-matching
``resolve_source`` this module carried through Phase 2, while the pointer itself named
no source. That resolver is retired with it.
"""

from __future__ import annotations

from collections.abc import Mapping

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
