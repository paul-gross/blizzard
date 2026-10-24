"""Chunk ingest — wrap ``{source, ref}`` pointers into a chunk pinned to a graph, storing the pointer
and never the contents.

**Batch = one chunk.** :func:`decide_ingest` owns what an ingest decides.

**One project.** :func:`choose_ingest_project` owns which project the chunk is minted into.

**Keys, not slugs.** A pointer's ``source`` is ``hub`` or a configured source's id — the registry resolved any slug
the token named before the pointer reached here. A refusal's text renders the source's current label, which the
service is handed (``bzh:domain-orchestration-split``); a refusal raised outside the service renders the key."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunk.model import Chunk, IReadWorkItemRepository, WorkRef, mint_chunk
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites
from blizzard.hub.domain.chunk.ports.record import IWriteChunkRecordRepository
from blizzard.hub.domain.chunk.ports.work_refs import IReadChunkWorkRefsRepository
from blizzard.hub.domain.graph.model import Graph
from blizzard.hub.domain.kernel.hub_source import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.projects.links import IReadProjectLinks
from blizzard.hub.domain.projects.model import ProjectId


class IWorkRefLabels(Protocol):
    """How a source and a pointer display — the work-source registry is one."""

    def source_label(self, source: str) -> str:
        """The handle the source keyed ``source`` displays under."""
        ...

    def label(self, pointer: WorkRef) -> str:
        """``pointer`` as displayed."""
        ...


class IngestConflict(Exception):
    """A submitted pointer is already held by a live chunk — the 409 carrier. ``label`` is the pointer as displayed,
    ``{source}#{ref}`` when none is given."""

    def __init__(self, *, existing_chunk_id: str, pointer: WorkRef, label: str | None = None) -> None:
        shown = label if label is not None else f"{pointer.source}#{pointer.ref}"
        super().__init__(f"pointer {shown} already held by live chunk {existing_chunk_id}")
        self.existing_chunk_id = existing_chunk_id
        self.pointer = pointer

    def labelled(self, labels: IWorkRefLabels) -> IngestConflict:
        """This refusal with its pointer rendered through ``labels``."""
        return IngestConflict(
            existing_chunk_id=self.existing_chunk_id, pointer=self.pointer, label=labels.label(self.pointer)
        )


class EmptyIngest(Exception):
    """An ingest named no work ref — a chunk wraps one or more."""

    def __init__(self) -> None:
        super().__init__("at least one token required")


class IngestProjectAmbiguous(Exception):
    """No project was named and the ingest's pointers do not agree on one — a configured source is linked
    to several projects, the sources' single projects differ, or hub items recorded in different
    projects. Carries the candidates, ordered by id."""

    def __init__(self, candidates: tuple[ProjectId, ...]) -> None:
        super().__init__(f"name a project (candidates: {', '.join(candidates)})")
        self.candidates = candidates


class IngestSourceNotLinked(Exception):
    """A pointer's configured source is not linked to the ingest's project — or, with ``project``
    ``None``, to any project, so no project could take the ingest."""

    def __init__(self, source: str, project: ProjectId | None, *, label: str | None = None) -> None:
        target = f"project {project}" if project is not None else "any project"
        super().__init__(f"source {label if label is not None else source} is not linked to {target}")
        self.source = source
        self.project = project

    def labelled(self, labels: IWorkRefLabels) -> IngestSourceNotLinked:
        """This refusal with its source rendered through ``labels``."""
        return IngestSourceNotLinked(self.source, self.project, label=labels.source_label(self.source))


class IngestProjectUnknown(Exception):
    """No project was named and nothing the ingest holds names one: a hub item it wraps records no
    project."""

    def __init__(self, pointer: WorkRef) -> None:
        super().__init__(f"name a project ({pointer.source}:{pointer.ref} records none)")
        self.pointer = pointer


class IngestItemProjectMismatch(Exception):
    """A hub item the ingest wraps records a project other than the one the ingest mints into."""

    def __init__(self, pointer: WorkRef, recorded: ProjectId, chosen: ProjectId) -> None:
        super().__init__(
            f"hub item {pointer.source}:{pointer.ref} records project {recorded}, not the ingest's project {chosen}"
        )
        self.pointer = pointer
        self.recorded = recorded
        self.chosen = chosen


def choose_ingest_project(
    named: ProjectId | None,
    pointers: Sequence[WorkRef],
    links: Mapping[str, Collection[ProjectId]],
    items_projects: Mapping[WorkRef, ProjectId],
) -> ProjectId:
    """The project an ingest of ``pointers`` mints into.

    ``named`` is the project the request named, already resolved. ``links`` maps each configured source
    to the projects a standing link draws from it (a source absent from it links none);
    ``items_projects`` maps each hub pointer to its item's recorded project. Unnamed, the project is
    the one every configured source is linked to alone; with no configured source, the one every hub
    item records. The hub source is linked to every project and sits out the inference, but its items
    must agree with the project chosen.

    Refuses the whole ingest: :class:`IngestSourceNotLinked` for a configured source linked to no
    project, or to the project named; :class:`IngestProjectAmbiguous` when the pointers disagree;
    :class:`IngestProjectUnknown` for a hub item recording no project when nothing else names one;
    :class:`IngestItemProjectMismatch` for a hub item recording a project other than the chosen one
    (a hub item recording none is not refused)."""
    configured = configured_sources(pointers)
    project = named if named is not None else _infer_project(configured, pointers, links, items_projects)
    for source in configured:
        if project not in links.get(source, ()):
            raise IngestSourceNotLinked(source, project)
    for pointer in pointers:
        recorded = items_projects.get(pointer)
        if pointer.source == RESERVED_HUB_SOURCE_NAME and recorded is not None and recorded != project:
            raise IngestItemProjectMismatch(pointer, recorded, project)
    return project


def configured_sources(pointers: Sequence[WorkRef]) -> list[str]:
    """The configured sources ``pointers`` draw from, in first-seen order — every source but the
    built-in hub source, which is linked to every project."""
    return list(dict.fromkeys(p.source for p in pointers if p.source != RESERVED_HUB_SOURCE_NAME))


def _infer_project(
    configured: Sequence[str],
    pointers: Sequence[WorkRef],
    links: Mapping[str, Collection[ProjectId]],
    items_projects: Mapping[WorkRef, ProjectId],
) -> ProjectId:
    """The one project the configured sources' links — or, with none, the hub items — agree on."""
    if configured:
        for source in configured:
            if not links.get(source):
                raise IngestSourceNotLinked(source, None)
        candidates = {project for source in configured for project in links[source]}
    else:
        candidates = set[ProjectId]()
        for pointer in pointers:
            recorded = items_projects.get(pointer)
            if recorded is None:
                raise IngestProjectUnknown(pointer)
            candidates.add(recorded)
    if len(candidates) != 1:
        raise IngestProjectAmbiguous(tuple(sorted(candidates)))
    return next(iter(candidates))


def ingest_work_refs(pointers: Iterable[WorkRef]) -> list[WorkRef]:
    """The work refs one ingest wraps: ``pointers`` with each repeat dropped, in first-seen order — two
    tokens naming one item (its id and its URL, say) wrap it once. Raises :class:`EmptyIngest` when
    none remain."""
    work_refs = list(dict.fromkeys(pointers))
    if not work_refs:
        raise EmptyIngest()
    return work_refs


def require_unheld(pointers: Iterable[WorkRef], live_holders: Mapping[WorkRef, str]) -> None:
    """Raise :class:`IngestConflict` for the first of ``pointers`` a live chunk holds — the
    at-most-one-live-holder rule every pointer-minting path shares. ``live_holders`` maps each held
    pointer to its live holder's id, as
    :meth:`~blizzard.hub.domain.chunk.ports.work_refs.IReadChunkWorkRefsRepository.live_holders` reads it."""
    for pointer in pointers:
        holder = live_holders.get(pointer)
        if holder is not None:
            raise IngestConflict(existing_chunk_id=holder, pointer=pointer)


def decide_ingest(
    pointers: Sequence[WorkRef],
    *,
    live_holders: Mapping[WorkRef, str],
    graph: Graph,
    project_id: ProjectId,
    at: datetime,
) -> Chunk:
    """The resting chunk of ``project_id`` an ingest of ``pointers`` mints on ``graph`` at ``at``, or the
    refusal: :class:`EmptyIngest` for no work ref, :class:`IngestConflict` for one a live chunk holds."""
    work_refs = ingest_work_refs(pointers)
    require_unheld(work_refs, live_holders)
    return mint_chunk(work_refs, graph_id=graph.graph_id, at=at, project_id=project_id)


def require_no_live_holder(work_refs: IReadChunkWorkRefsRepository, pointer: WorkRef) -> None:
    """Read ``pointer``'s live holder and apply :func:`require_unheld` — the single-pointer read the
    item-minting paths share."""
    holder = work_refs.find_live_holder(pointer)
    require_unheld([pointer], {pointer: holder} if holder is not None else {})


class IngestService:
    """Mint a chunk from work refs into one project, pinned to the default graph."""

    def __init__(
        self,
        *,
        record: IWriteChunkRecordRepository,
        exclusive: IChunkExclusiveWrites,
        links: IReadProjectLinks,
        items: IReadWorkItemRepository,
        labels: IWorkRefLabels,
        clock: IClock,
    ) -> None:
        self._record = record
        self._labels = labels
        # The locked-transaction seam (``bzh:store-exclusive-write``): whether a pointer is held
        # is read under the pointer's lock, on the connection the mint then writes on.
        self._exclusive = exclusive
        self._links = links
        self._items = items
        self._clock = clock

    def choose_project(self, pointers: Sequence[WorkRef], project: ProjectId | None) -> ProjectId:
        """The project an ingest of ``pointers`` naming ``project`` mints into, read before anything
        is written — :func:`choose_ingest_project` over the configured sources' standing links and
        the hub items' recorded projects. Raises its refusals and
        :class:`EmptyIngest`."""
        work_refs = ingest_work_refs(pointers)
        configured = configured_sources(work_refs)
        linking = self._links.projects_linking_sources(configured)
        links = {source: linking.get(source, []) for source in configured}
        try:
            return choose_ingest_project(project, work_refs, links, self._recorded_projects(work_refs))
        except IngestSourceNotLinked as exc:
            raise exc.labelled(self._labels) from None

    def ingest(self, pointers: Sequence[WorkRef], *, graph: Graph, project: ProjectId | None) -> str:
        """Mint a chunk wrapping ``pointers`` into ``project`` — or, with ``None``, the project
        :func:`choose_ingest_project` infers. Raises :class:`EmptyIngest` for no pointer, the
        project refusals of :func:`choose_ingest_project`, and :class:`IngestConflict` for a pointer
        a live chunk holds. Of two overlapping ingests of one pointer, exactly one mints.

        The links are read once to choose the project, then again under that project's source-link
        lock — taken before the pointers' — so a source unlinked in between refuses the ingest
        rather than minting a chunk its project no longer draws from."""
        work_refs = ingest_work_refs(pointers)
        chosen = self.choose_project(work_refs, project)
        try:
            with self._exclusive.locked_work_refs(work_refs, project=chosen) as handle:
                choose_ingest_project(chosen, work_refs, handle.source_links(configured_sources(work_refs)), {})
                chunk = decide_ingest(
                    work_refs,
                    live_holders=handle.live_holders(work_refs),
                    graph=graph,
                    project_id=chosen,
                    at=self._clock.now(),
                )
                self._record.mint_locked(handle, chunk)
        except (IngestSourceNotLinked, IngestConflict) as exc:
            raise exc.labelled(self._labels) from None
        return chunk.chunk_id

    def _recorded_projects(self, work_refs: Sequence[WorkRef]) -> dict[WorkRef, ProjectId]:
        """The hub items' recorded projects — they decide the project when nothing else names one, and
        must agree with it otherwise."""
        hub_refs = [pointer for pointer in work_refs if pointer.source == RESERVED_HUB_SOURCE_NAME]
        if not hub_refs:
            return {}
        return {
            pointer: item.project_id
            for pointer, item in self._items.get_many(hub_refs).items()
            if item.project_id is not None
        }
