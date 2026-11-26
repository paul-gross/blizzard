"""The work source registry — configured sources read from their records on every call.

The store is the only answer to what is configured (``bzh:config-read-on-use``): each
lookup reads the record's revisions and hands out the adapter built under them from the
process's ``ConfigObjectCache`` (``src/blizzard/hub/live_config/__init__.py``), so an edit, a retirement or
a secret replace reaches the next call with no restart. The built-in ``hub`` source is
seated in-process, outside the cache — never configured, never retired.

Every source past the edge is named by its key: ``hub``, or a configured source's id. Only
``resolve`` reads a slug — the prefix of an ingest token — and the labels render each source's
current slug, so a rename reaches every displayed work ref with no stored ref rewritten.

``resolve``, ``names`` and ``annotating_ids`` read active records; ``get``,
``annotator``, ``closer``, ``label_clearer``, ``bindings`` and labels can read retired records.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Protocol, cast

from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.chunk.ports.work_sources import (
    IWorkAnnotator,
    IWorkCloser,
    WorkAnnotateError,
    WorkAnnotateRefusal,
)
from blizzard.hub.domain.config.changes import RecordKind
from blizzard.hub.domain.config.revisions import ConfigRevisions
from blizzard.hub.domain.config.work_sources import ConfiguredWorkSource, IReadWorkSourceRepository, WorkSourceId
from blizzard.hub.domain.kernel.hub_source import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.live_config import ConfigObjectCache
from blizzard.hub.work_sources.editor import IWorkEditor
from blizzard.hub.work_sources.source import (
    IWorkSource,
    IWorkSourceBatchReader,
    IWorkSourceBindings,
    IWorkSourceRegistry,
    WorkSourceAuthRefused,
    WorkSourceError,
)


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


def label_from_binding(pointer: WorkRef, source: IWorkSource | None, fallback_slug: Callable[[], str]) -> str:
    """Render the binding's label, resolving its current slug only for a ``None`` label."""
    rendered = source.label(pointer) if source is not None else None
    return rendered if rendered is not None else f"{fallback_slug()}#{pointer.ref}"


def _unless_retired(
    hand_out: Callable[[], BuiltWorkSource | None], record: Callable[[], ConfiguredWorkSource | None]
) -> BuiltWorkSource | None:
    """``hand_out()``'s adapter, a build failure of a retired source reading as ``None``."""
    try:
        return hand_out()
    except WorkSourceError:
        found = record()
        if found is not None and found.retired:
            return None
        raise


def _annotate_refusal(exc: WorkSourceAuthRefused, record: ConfiguredWorkSource | None) -> WorkAnnotateError:
    """The annotation refusal a build the forge refused the credential of is, for a write half."""
    # The build just read it, and a configured record is never deleted.
    assert record is not None
    return WorkAnnotateError(str(exc), refusal=WorkAnnotateRefusal(exc.classified, record.fields.forge_id))


def _enabled(close_forge_writes_enabled: bool | Callable[[], bool]) -> bool:
    return close_forge_writes_enabled() if callable(close_forge_writes_enabled) else close_forge_writes_enabled


class StoreWorkSourceRegistry:
    """``build`` turns a record into its adapter, revealing its secret; failure raises
    :class:`~blizzard.hub.work_sources.source.WorkSourceError` for an active source.
    ``close_forge_writes_enabled=False`` seats no closer for a configured source;
    the built-in source remains a closer."""

    def __init__(
        self,
        *,
        records: IReadWorkSourceRepository,
        objects: ConfigObjectCache,
        build: Callable[[ConfiguredWorkSource], BuiltWorkSource],
        built_in: IBuiltInWorkSource,
        close_forge_writes_enabled: bool | Callable[[], bool] = True,
    ) -> None:
        self._records = records
        self._objects = objects
        self._build = build
        self._built_in = built_in
        self._close_forge_writes_enabled = close_forge_writes_enabled

    def _built(self, source: str) -> BuiltWorkSource | None:
        """The adapter of the source keyed ``source``, or ``None`` when it is unknown — or retired with a secret that
        no longer reveals: nothing stops that secret being retired once the source is, and the read paths over items
        already ingested must not fail on it. An active source that cannot be built raises. The adapter is cached
        under the id and rebuilt when the record's revision moves, a slug edit among them."""
        return self._handed(
            source, lambda: self._objects.get(RecordKind.WORK_SOURCE, source, partial(self._build_current, source))
        )

    def _built_under(self, source: str, current: ConfigRevisions | None) -> BuiltWorkSource | None:
        """:meth:`_built` against ``current``, the source's revisions the caller read ahead."""
        return self._handed(
            source,
            lambda: self._objects.get_under(
                RecordKind.WORK_SOURCE, source, current, partial(self._build_current, source)
            ),
        )

    def _handed(self, source: str, hand_out: Callable[[], BuiltWorkSource | None]) -> BuiltWorkSource | None:
        return _unless_retired(hand_out, lambda: self._records.get(WorkSourceId(source)))

    def _build_current(self, source: str) -> BuiltWorkSource:
        record = self._records.get(WorkSourceId(source))
        # The revisions read just found it, and a configured record is never deleted.
        assert record is not None
        return self._build(record)

    def get(self, source: str) -> IWorkSource | None:
        if source == RESERVED_HUB_SOURCE_NAME:
            return self._built_in
        built = self._built(source)
        return built.source if built is not None else None

    def names(self) -> list[str]:
        return [record.slug for record in self._records.list_all(include_retired=False)] + [RESERVED_HUB_SOURCE_NAME]

    def source_label(self, source: str) -> str:
        if source == RESERVED_HUB_SOURCE_NAME:
            return source
        record = self._records.get(WorkSourceId(source))
        return record.slug if record is not None else source

    def label(self, pointer: WorkRef) -> str:
        source = self.get(pointer.source)
        return label_from_binding(pointer, source, lambda: self.source_label(pointer.source))

    def _annotating(self, source: str) -> BuiltWorkSource | None:
        """:meth:`_built` for a write half: a build the forge refused the credential of is an annotation refusal."""
        try:
            return self._built(source)
        except WorkSourceAuthRefused as exc:
            raise _annotate_refusal(exc, self._records.get(WorkSourceId(source))) from exc

    def annotator(self, source: str) -> IWorkAnnotator | None:
        if source == RESERVED_HUB_SOURCE_NAME:
            return None
        built = self._annotating(source)
        if built is None or not built.record.fields.annotate:
            return None
        return cast(IWorkAnnotator, built.source)

    def annotating_ids(self) -> list[str]:
        return [
            record.work_source_id for record in self._records.list_all(include_retired=False) if record.fields.annotate
        ]

    def label_clearer(self, source: str) -> IWorkAnnotator | None:
        if source == RESERVED_HUB_SOURCE_NAME:
            return None
        built = self._annotating(source)
        return cast(IWorkAnnotator, built.source) if built is not None else None

    def closer(self, source: str) -> IWorkCloser | None:
        if source == RESERVED_HUB_SOURCE_NAME:
            return self._built_in
        if not _enabled(self._close_forge_writes_enabled):
            return None
        built = self._built(source)
        return cast(IWorkCloser, built.source) if built is not None else None

    def editor(self, source: str) -> IWorkEditor | None:
        return self._built_in if source == RESERVED_HUB_SOURCE_NAME else None

    def batch_reader(self, source: str) -> IWorkSourceBatchReader | None:
        bound = self.get(source)
        return bound if isinstance(bound, IWorkSourceBatchReader) else None

    def bindings(self, sources: Iterable[str]) -> IWorkSourceBindings:
        bound = frozenset(sources)
        configured = sorted(bound - {RESERVED_HUB_SOURCE_NAME})
        # Revisions before records: a record a build reads must be no older than the revisions it is cached under.
        current = self._objects.revisions_for(RecordKind.WORK_SOURCE, configured)
        records = self._records.get_many([WorkSourceId(source) for source in configured])
        return WorkSourceBindings(
            bound,
            current=current,
            records={str(key): record for key, record in records.items()},
            objects=self._objects,
            build=self._build,
            built_in=self._built_in,
            close_forge_writes_enabled=self._close_forge_writes_enabled,
        )

    def resolve(self, token: str) -> WorkRef | None:
        for sep in (":", "#"):
            prefix, found, ref = token.partition(sep)
            if not found:
                continue
            if prefix == RESERVED_HUB_SOURCE_NAME:
                return self._built_in.parse(token)
            record = self._records.resolve(prefix)
            if record is not None:
                if record.retired:
                    return None
                built = self._built(record.work_source_id)
                return built.source.parse(f"{record.work_source_id}{sep}{ref}") if built is not None else None
        # Every active source's revisions in one read; each is still compared and built only when its turn comes,
        # so a source claiming the token spares building those after it.
        active = self._records.list_all(include_retired=False)
        current = self._objects.revisions_for(RecordKind.WORK_SOURCE, [record.work_source_id for record in active])
        for record in active:
            built = self._built_under(record.work_source_id, current.get(record.work_source_id))
            pointer = built.source.parse(token) if built is not None else None
            if pointer is not None:
                return pointer
        return self._built_in.parse(token)


_work_source_registry_conforms: type[IWorkSourceRegistry] = StoreWorkSourceRegistry


class WorkSourceBindings:
    """:meth:`StoreWorkSourceRegistry.bindings`' answer: ``current`` and ``records`` are the revisions and records of
    the bound sources as read ahead, a source absent from them unknown. Each adapter is handed out once."""

    def __init__(
        self,
        sources: frozenset[str],
        *,
        current: Mapping[str, ConfigRevisions],
        records: Mapping[str, ConfiguredWorkSource],
        objects: ConfigObjectCache,
        build: Callable[[ConfiguredWorkSource], BuiltWorkSource],
        built_in: IBuiltInWorkSource,
        close_forge_writes_enabled: bool | Callable[[], bool],
    ) -> None:
        self._sources = sources
        self._current = current
        self._records = records
        self._objects = objects
        self._build = build
        self._built_in = built_in
        self._close_forge_writes_enabled = close_forge_writes_enabled
        self._handed: dict[str, BuiltWorkSource | None] = {}

    def _built(self, source: str) -> BuiltWorkSource | None:
        """:meth:`StoreWorkSourceRegistry._built` over what was read ahead."""
        if source not in self._handed:
            self._handed[source] = _unless_retired(
                lambda: self._objects.get_under(
                    RecordKind.WORK_SOURCE,
                    source,
                    self._current.get(source),
                    lambda: self._build(self._records[source]),
                ),
                lambda: self._records.get(source),
            )
        return self._handed[source]

    def _is_built_in(self, source: str) -> bool:
        if source not in self._sources:
            raise KeyError(source)
        return source == RESERVED_HUB_SOURCE_NAME

    def get(self, source: str) -> IWorkSource | None:
        if self._is_built_in(source):
            return self._built_in
        built = self._built(source)
        return built.source if built is not None else None

    def source_label(self, source: str) -> str:
        if self._is_built_in(source):
            return source
        record = self._records.get(source)
        return record.slug if record is not None else source

    def label(self, pointer: WorkRef) -> str:
        source = self.get(pointer.source)
        return label_from_binding(pointer, source, lambda: self.source_label(pointer.source))

    def _annotating(self, source: str) -> BuiltWorkSource | None:
        try:
            return self._built(source)
        except WorkSourceAuthRefused as exc:
            raise _annotate_refusal(exc, self._records.get(source)) from exc

    def annotator(self, source: str) -> IWorkAnnotator | None:
        if self._is_built_in(source):
            return None
        built = self._annotating(source)
        if built is None or not built.record.fields.annotate:
            return None
        return cast(IWorkAnnotator, built.source)

    def label_clearer(self, source: str) -> IWorkAnnotator | None:
        if self._is_built_in(source):
            return None
        built = self._annotating(source)
        return cast(IWorkAnnotator, built.source) if built is not None else None

    def closer(self, source: str) -> IWorkCloser | None:
        if self._is_built_in(source):
            return self._built_in
        if not _enabled(self._close_forge_writes_enabled):
            return None
        built = self._built(source)
        return cast(IWorkCloser, built.source) if built is not None else None


_work_source_bindings_conforms: type[IWorkSourceBindings] = WorkSourceBindings
