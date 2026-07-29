"""The work source registry — configured sources looked up by name.

A plain, dependency-free ``dict`` wrapper (``bzh:domain-core``, no I/O): the
credentialed clients live behind each entry's adapter, built at the composition root
(:mod:`blizzard.hub.work_sources.internal.factory`). An empty registry is a legal hub with no work-source
reach — the pass-through routes degrade per-chunk/per-pointer rather than refusing to
start.

The pointer carries its own ``source`` name, so finding a pointer's binding is a
plain lookup — ``registry.get(pointer.source)`` — rather than the older repo-matching
``resolve_source`` this module carried while the pointer itself named no source. That
resolver is retired with it.

:meth:`resolve` is the intake-side counterpart: an ingest **token** (as
opposed to an already-resolved pointer's ``source`` name) is tried against every
configured binding's own :meth:`~blizzard.hub.work_sources.source.IWorkSource.parse` in turn, first
claim wins. Config guarantees at most one claim (a unique ``name``, and no two sources
sharing a ``(provider, repo)``), so registration order never matters in practice.
"""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.hub.domain.work import WorkRef
from blizzard.hub.work_sources.annotator import IWorkAnnotator
from blizzard.hub.work_sources.source import IWorkSource, IWorkSourceRegistry


class WorkSourceRegistry:
    """The hub's configured work sources, keyed by their declared ``name``.

    ``annotators`` is a strict subset of ``sources`` — only a source config with
    ``annotate = true`` gets an entry here (built by the factory); a name absent
    from ``annotators`` has no write half at all, which is what makes "never
    written to" a property of the object graph rather than a branch.
    """

    def __init__(
        self,
        sources: Mapping[str, IWorkSource] | None = None,
        annotators: Mapping[str, IWorkAnnotator] | None = None,
    ) -> None:
        self._sources = dict(sources or {})
        self._annotators = dict(annotators or {})

    def get(self, name: str) -> IWorkSource | None:
        return self._sources.get(name)

    def names(self) -> list[str]:
        return list(self._sources.keys())

    def annotator(self, name: str) -> IWorkAnnotator | None:
        return self._annotators.get(name)

    def annotating_names(self) -> list[str]:
        return list(self._annotators.keys())

    def resolve(self, token: str) -> WorkRef | None:
        """The first configured binding's ``parse`` of ``token`` that claims it, or
        ``None`` when none do."""
        for source in self._sources.values():
            pointer = source.parse(token)
            if pointer is not None:
                return pointer
        return None


def _conforms_work_source_registry(x: WorkSourceRegistry) -> IWorkSourceRegistry:
    return x
