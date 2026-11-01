"""The pass-through read of work items — one path for one chunk's pointers or a whole batch's.

Contents are never stored: every call reads each distinct pointer fresh from its source. The calls a read makes are
bounded by the sources, not the pointers — one batch read per source whose binding has one
(:class:`~blizzard.hub.work_sources.source.IWorkSourceBatchReader`), else one ``fetch`` per pointer. A failure,
unconfigured source or unresolvable pointer is that pointer's own :class:`ReadWorkItem` error, never the read's."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.foundation.work_items import WorkItemPriority
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.kernel.hub_source import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.work_sources.source import AuthorView, IWorkSourceRegistry, WorkItem, WorkSourceError


@domain_model
@dataclass(frozen=True)
class ReadWorkItem:
    """One pointer as read: how it displays, and either the item or the reason it could not be read."""

    pointer: WorkRef
    slug: str
    label: str
    hub_source: bool
    web_url: str | None = None
    title: str | None = None
    body: str | None = None
    comments: Sequence[str] = ()
    author: AuthorView | None = None
    stated_priority: WorkItemPriority | None = None
    error: str | None = None


def read_work_items(
    chunk_refs: Mapping[str, Sequence[WorkRef]],
    *,
    sources: IWorkSourceRegistry,
    live_holders: Mapping[WorkRef, str],
    include_comments: bool,
) -> dict[str, list[ReadWorkItem]]:
    """Every chunk's pointers read, in each chunk's own order. ``live_holders`` is the caller's one read of which live
    chunk holds each pointer, for the address of a source whose address depends on it."""
    distinct = list(dict.fromkeys(ref for refs in chunk_refs.values() for ref in refs))
    by_source: dict[str, list[WorkRef]] = {}
    for ref in distinct:
        by_source.setdefault(ref.source, []).append(ref)
    read: dict[WorkRef, ReadWorkItem] = {}
    for source, refs in by_source.items():
        read.update(_read_source(source, refs, sources, live_holders, include_comments))
    return {chunk_id: [read[ref] for ref in refs] for chunk_id, refs in chunk_refs.items()}


def _read_source(
    source: str,
    refs: Sequence[WorkRef],
    sources: IWorkSourceRegistry,
    live_holders: Mapping[WorkRef, str],
    include_comments: bool,
) -> dict[WorkRef, ReadWorkItem]:
    slug = sources.source_label(source)
    hub_source = source == RESERVED_HUB_SOURCE_NAME
    try:
        binding = sources.get(source)
        if binding is None:
            message = f"no work source is configured under {source!r}"
            return {ref: ReadWorkItem(ref, slug, sources.label(ref), hub_source, error=message) for ref in refs}
        batch = sources.batch_reader(source)
        outcomes: Mapping[WorkRef, WorkItem | WorkSourceError] = (
            batch.fetch_many(refs, include_comments=include_comments) if batch is not None else {}
        )
        result: dict[WorkRef, ReadWorkItem] = {}
        for ref in refs:
            label = sources.label(ref)
            web_url = binding.web_url(ref, live_holder=live_holders.get(ref))
            outcome: WorkItem | WorkSourceError | ValueError
            if batch is not None:
                outcome = outcomes.get(ref) or WorkSourceError(f"{label}: the source returned no answer")
            else:
                try:
                    outcome = binding.fetch(ref)
                except (WorkSourceError, ValueError) as exc:
                    outcome = exc
            result[ref] = _entry(ref, slug, label, hub_source, web_url, outcome, include_comments)
        return result
    except WorkSourceError as exc:
        return {ref: ReadWorkItem(ref, slug, f"{slug}#{ref.ref}", hub_source, error=str(exc)) for ref in refs}


def _entry(
    ref: WorkRef,
    slug: str,
    label: str,
    hub_source: bool,
    web_url: str | None,
    outcome: WorkItem | WorkSourceError | ValueError,
    include_comments: bool,
) -> ReadWorkItem:
    if isinstance(outcome, WorkItem):
        try:
            priority = WorkItemPriority(outcome.stated_priority) if outcome.stated_priority is not None else None
        except ValueError as exc:
            return ReadWorkItem(ref, slug, label, hub_source, web_url, error=str(exc))
        return ReadWorkItem(
            ref,
            slug,
            label,
            hub_source,
            web_url,
            title=outcome.title,
            body=outcome.body,
            comments=tuple(outcome.comments) if include_comments else (),
            author=outcome.author,
            stated_priority=priority,
        )
    return ReadWorkItem(ref, slug, label, hub_source, web_url, error=str(outcome))
