"""The PM source registry — configured sources looked up by name.

A plain, dependency-free ``dict`` wrapper (``bzh:domain-core``, no I/O): the
credentialed clients live behind each entry's adapter, built at the composition root
(:mod:`blizzard.hub.pm.internal.factory`). An empty registry is a legal hub with no PM
reach — the pass-through routes degrade per-chunk/per-pointer rather than refusing to
start.

D-107 gives the pointer its own ``source`` name, so finding a pointer's binding is a
plain lookup — ``registry.get(pointer.source)`` — rather than the D-109 repo-matching
``resolve_source`` this module carried through Phase 2, while the pointer itself named
no source. That resolver is retired with it.

D-111 adds :meth:`resolve`, the intake-side counterpart: an ingest **token** (as
opposed to an already-resolved pointer's ``source`` name) is tried against every
configured binding's own :meth:`~blizzard.hub.pm.source.IPmSource.parse` in turn, first
claim wins. Config guarantees at most one claim (a unique ``name``, and no two sources
sharing a ``(provider, repo)``), so registration order never matters in practice.
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

    def resolve(self, token: str) -> PmPointer | None:
        """The first configured binding's ``parse`` of ``token`` that claims it, or
        ``None`` when none do."""
        for source in self._sources.values():
            pointer = source.parse(token)
            if pointer is not None:
                return pointer
        return None


def _conforms_pm_source_registry(x: PmSourceRegistry) -> IPmSourceRegistry:
    return x
