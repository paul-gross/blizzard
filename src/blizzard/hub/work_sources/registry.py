"""The work source registry — configured sources read from their records on every call.

The store is the only answer to what is configured (``bzh:config-read-on-use``): each
lookup reads the record's revisions and hands out the adapter built under them from the
process's :class:`~blizzard.hub.live_config.ConfigObjectCache`, so an edit, a retirement or
a secret replace reaches the next call with no restart. The built-in ``hub`` source is
seated in-process, outside the cache — never configured, never retired.

``resolve``, ``names`` and ``annotating_names`` read the active set, so ingest naming a
retired source is refused and the annotation sweep lets go of its labels; ``get``,
``annotator``, ``closer`` and ``label_clearer`` reach retired records too, so items already
ingested from one still label, close and annotate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast

from blizzard.hub.config import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.config.changes import RecordKind
from blizzard.hub.domain.config.work_sources import ConfiguredWorkSource, IReadWorkSourceRepository
from blizzard.hub.live_config import ConfigObjectCache
from blizzard.hub.work_sources.annotator import IWorkAnnotator
from blizzard.hub.work_sources.closer import IWorkCloser
from blizzard.hub.work_sources.editor import IWorkEditor
from blizzard.hub.work_sources.source import IWorkSource, IWorkSourceRegistry, WorkSourceError


class IBuiltInWorkSource(IWorkSource, IWorkEditor, IWorkCloser, Protocol):
    """The built-in ``hub`` binding — a source, an editor and a closer at once."""


@dataclass(frozen=True)
class BuiltWorkSource:
    """An adapter with the record it was built from; closing it releases the adapter's client."""

    record: ConfiguredWorkSource
    source: IWorkSource
    release: Callable[[], None]

    def close(self) -> None:
        self.release()


class StoreWorkSourceRegistry:
    """``build`` turns a record into its adapter, revealing the record's secret; it raises
    :class:`~blizzard.hub.work_sources.source.WorkSourceError` when it cannot, so a source
    that exists never reads as unconfigured. ``close_forge_writes_enabled=False`` seats no
    closer for a configured source (never the built-in one)."""

    def __init__(
        self,
        *,
        records: IReadWorkSourceRepository,
        objects: ConfigObjectCache,
        build: Callable[[ConfiguredWorkSource], BuiltWorkSource],
        built_in: IBuiltInWorkSource,
        close_forge_writes_enabled: bool = True,
    ) -> None:
        self._records = records
        self._objects = objects
        self._build = build
        self._built_in = built_in
        self._close_forge_writes_enabled = close_forge_writes_enabled

    def _built(self, name: str) -> BuiltWorkSource | None:
        """The source's adapter, or ``None`` when it is unknown — or retired with a secret that no
        longer reveals: nothing stops that secret being retired once the source is, and the read
        paths over items already ingested must not fail on it. An active source that cannot be built raises."""
        try:
            return self._objects.get(RecordKind.WORK_SOURCE, name, lambda: self._build_current(name))
        except WorkSourceError:
            record = self._records.get(name)
            if record is not None and record.retired:
                return None
            raise

    def _build_current(self, name: str) -> BuiltWorkSource:
        record = self._records.get(name)
        # The revisions read just found it, and a configured record is never deleted.
        assert record is not None
        return self._build(record)

    def get(self, name: str) -> IWorkSource | None:
        if name == RESERVED_HUB_SOURCE_NAME:
            return self._built_in
        built = self._built(name)
        return built.source if built is not None else None

    def names(self) -> list[str]:
        return [record.name for record in self._records.list_all(include_retired=False)] + [RESERVED_HUB_SOURCE_NAME]

    def annotator(self, name: str) -> IWorkAnnotator | None:
        if name == RESERVED_HUB_SOURCE_NAME:
            return None
        built = self._built(name)
        if built is None or not built.record.fields.annotate:
            return None
        return cast(IWorkAnnotator, built.source)

    def annotating_names(self) -> list[str]:
        return [record.name for record in self._records.list_all(include_retired=False) if record.fields.annotate]

    def label_clearer(self, name: str) -> IWorkAnnotator | None:
        if name == RESERVED_HUB_SOURCE_NAME:
            return None
        built = self._built(name)
        return cast(IWorkAnnotator, built.source) if built is not None else None

    def closer(self, name: str) -> IWorkCloser | None:
        if name == RESERVED_HUB_SOURCE_NAME:
            return self._built_in
        if not self._close_forge_writes_enabled:
            return None
        built = self._built(name)
        return cast(IWorkCloser, built.source) if built is not None else None

    def editor(self, name: str) -> IWorkEditor | None:
        return self._built_in if name == RESERVED_HUB_SOURCE_NAME else None

    def resolve(self, token: str) -> WorkRef | None:
        """The first active binding's ``parse`` of ``token`` that claims it, the built-in
        source last; ``None`` when none do."""
        for record in self._records.list_all(include_retired=False):
            built = self._built(record.name)
            pointer = built.source.parse(token) if built is not None else None
            if pointer is not None:
                return pointer
        return self._built_in.parse(token)


def _conforms_work_source_registry(x: StoreWorkSourceRegistry) -> IWorkSourceRegistry:
    return x
