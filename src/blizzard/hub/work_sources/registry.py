"""The work source registry — configured sources looked up by name.

A dependency-free ``dict`` wrapper (``bzh:domain-core``); an empty registry is a legal
hub. :meth:`resolve` tries an ingest token against every binding's ``parse`` in turn,
first claim wins — config guarantees at most one claim, so order never matters.
"""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.hub.domain.work import WorkRef
from blizzard.hub.work_sources.annotator import IWorkAnnotator
from blizzard.hub.work_sources.closer import IWorkCloser
from blizzard.hub.work_sources.editor import IWorkEditor
from blizzard.hub.work_sources.source import IWorkSource, IWorkSourceRegistry


class WorkSourceRegistry:
    """The hub's configured work sources, keyed by their declared ``name``.

    ``annotators``/``closers``/``editors`` are each a subset of ``sources``: an absent
    name has no write half, making "never written to" a property of the object graph."""

    def __init__(
        self,
        sources: Mapping[str, IWorkSource] | None = None,
        annotators: Mapping[str, IWorkAnnotator] | None = None,
        closers: Mapping[str, IWorkCloser] | None = None,
        editors: Mapping[str, IWorkEditor] | None = None,
    ) -> None:
        self._sources = dict(sources or {})
        self._annotators = dict(annotators or {})
        self._closers = dict(closers or {})
        self._editors = dict(editors or {})

    def get(self, name: str) -> IWorkSource | None:
        return self._sources.get(name)

    def names(self) -> list[str]:
        return list(self._sources.keys())

    def annotator(self, name: str) -> IWorkAnnotator | None:
        return self._annotators.get(name)

    def annotating_names(self) -> list[str]:
        return list(self._annotators.keys())

    def closer(self, name: str) -> IWorkCloser | None:
        return self._closers.get(name)

    def closing_names(self) -> list[str]:
        return list(self._closers.keys())

    def editor(self, name: str) -> IWorkEditor | None:
        return self._editors.get(name)

    def editing_names(self) -> list[str]:
        return list(self._editors.keys())

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
