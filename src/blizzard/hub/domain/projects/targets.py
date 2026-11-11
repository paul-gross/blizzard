"""A chunk's repository targets: the branch each repository its project links is started from and landed into.

A repository's **effective branch** for a chunk is the one the chunk captured, else its link's own branch, else its
record's ``base_branch``. A chunk captures each repository once, the first time anything needs it — its claim, or a
delivery that resolves a repository the claim did not — and never again, so a later link edit reaches only chunks that
have not yet captured that repository. A captured target is an immutable fact; read-only paths fall back to the live
branch without writing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    IReadRepositoryRecordRepository,
    RepositoryId,
)
from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction
from blizzard.hub.domain.projects.links import IReadProjectLinks, RepositoryLink
from blizzard.hub.domain.projects.model import ProjectId


@domain_model
@dataclass(frozen=True)
class RepositoryTarget:
    """One repository's branch for one chunk: ``inherited`` when it is the repository record's own, not the link's
    override. ``captured_at`` is when the chunk captured it, ``None`` while it is a live preview."""

    repository_id: RepositoryId
    base_branch: str
    inherited: bool
    captured_at: datetime | None = None

    def captured(self, at: datetime) -> RepositoryTarget:
        """This target as the chunk captures it at ``at``."""
        return replace(self, captured_at=at)


type Targets = Mapping[RepositoryId, RepositoryTarget]


def live_targets(
    links: Iterable[RepositoryLink], records: Mapping[RepositoryId, ConfiguredRepository]
) -> dict[RepositoryId, RepositoryTarget]:
    """Each standing link's live target, for a linked record that is enabled: the link's own branch, else the
    record's."""
    targets: dict[RepositoryId, RepositoryTarget] = {}
    for link in links:
        record = records.get(link.repository_id)
        if record is None or record.retired_at is not None:
            continue
        targets[link.repository_id] = RepositoryTarget(
            link.repository_id, link.effective_base_branch(record), inherited=link.base_branch is None
        )
    return targets


def effective_targets(captured: Targets, live: Targets) -> dict[RepositoryId, RepositoryTarget]:
    """A chunk's effective targets: each captured one, else the live one."""
    return {**live, **captured}


def names_override(targets: Targets) -> bool:
    """Whether any of ``targets`` is a project link's own branch rather than its repository record's."""
    return any(not target.inherited for target in targets.values())


def prepared_at(targets: Targets, prepared: Mapping[str, str] | None) -> bool:
    """Whether environments ``prepared`` at these branches, by repository id, stand at ``targets``: the same branch
    for the same repositories. ``None`` is a runner that names no targets, which stands at them only when none is an
    override — it prepares every repository at its workspace's own branch."""
    if prepared is None:
        return not names_override(targets)
    return branches(targets) == dict(prepared)


def branches(targets: Targets) -> dict[str, str]:
    """``targets`` as the branch of each repository id, the shape delivery's resolution reads."""
    return {repository_id: target.base_branch for repository_id, target in targets.items()}


@domain_model
@dataclass(frozen=True)
class TargetChunk:
    """The chunk whose targets are read: its id and the project whose links it reads live."""

    chunk_id: str
    project_id: ProjectId | None


class IReadChunkTargets(Protocol):
    """Reads of the targets chunks captured, within one tenant."""

    def captured_for(
        self, chunk_ids: Sequence[str], *, transaction: Transaction | None = None
    ) -> dict[str, dict[RepositoryId, RepositoryTarget]]:
        """Each chunk's captured targets by repository id; a chunk that captured none is dropped. Read in
        ``transaction`` when given."""
        ...


class IWriteChunkTargets(IReadChunkTargets, Protocol):
    def capture(
        self, chunk_id: str, targets: Sequence[RepositoryTarget], *, transaction: LockedTransaction
    ) -> dict[RepositoryId, RepositoryTarget]:
        """Insert each of ``targets`` the chunk has not captured yet, in ``transaction``, which locks the chunk; a
        captured target is never rewritten. Answers every target the chunk holds afterwards."""
        ...


class ChunkTargets:
    """The one home of "a chunk's effective targets": the read every caller makes, and the capture."""

    def __init__(
        self, *, targets: IWriteChunkTargets, links: IReadProjectLinks, records: IReadRepositoryRecordRepository
    ) -> None:
        self._targets = targets
        self._links = links
        self._records = records

    def live_for(
        self, project_ids: Iterable[ProjectId | None]
    ) -> dict[ProjectId, dict[RepositoryId, RepositoryTarget]]:
        """Each project's live targets, in a bounded number of reads (``bzh:bulk-reconstitution``)."""
        ids = sorted({project_id for project_id in project_ids if project_id is not None})
        if not ids:
            return {}
        links = self._links.repositories_of_projects(ids)
        linked = sorted({link.repository_id for rows in links.values() for link in rows})
        records = self._records.get_many(linked) if linked else {}
        return {project_id: live_targets(links.get(project_id, []), records) for project_id in ids}

    def effective_for(self, chunks: Sequence[TargetChunk]) -> dict[str, dict[RepositoryId, RepositoryTarget]]:
        """Each chunk's effective targets, read without writing, in a bounded number of reads."""
        if not chunks:
            return {}
        captured = self._targets.captured_for([chunk.chunk_id for chunk in chunks])
        live = self.live_for(chunk.project_id for chunk in chunks)
        return {
            chunk.chunk_id: effective_targets(
                captured.get(chunk.chunk_id, {}), live.get(chunk.project_id, {}) if chunk.project_id else {}
            )
            for chunk in chunks
        }

    def effective_locked(
        self, chunk: TargetChunk, *, transaction: LockedTransaction
    ) -> dict[RepositoryId, RepositoryTarget]:
        """:meth:`effective` with the captured targets read in ``transaction``, which locks the chunk."""
        captured = self._targets.captured_for([chunk.chunk_id], transaction=transaction).get(chunk.chunk_id, {})
        live = self.live_for([chunk.project_id]).get(chunk.project_id, {}) if chunk.project_id else {}
        return effective_targets(captured, live)

    def effective(self, chunk: TargetChunk) -> dict[RepositoryId, RepositoryTarget]:
        """:meth:`effective_for` for one chunk."""
        return self.effective_for([chunk])[chunk.chunk_id]

    def capture_live(
        self, chunk: TargetChunk, *, at: datetime, transaction: LockedTransaction
    ) -> dict[RepositoryId, RepositoryTarget]:
        """Capture, in ``transaction``, every live target of the chunk's project it has not captured yet — a claim's
        capture. Answers the chunk's captured targets."""
        live = self.live_for([chunk.project_id]).get(chunk.project_id, {}) if chunk.project_id else {}
        return self.capture(chunk.chunk_id, live.values(), at=at, transaction=transaction)

    def capture(
        self, chunk_id: str, targets: Iterable[RepositoryTarget], *, at: datetime, transaction: LockedTransaction
    ) -> dict[RepositoryId, RepositoryTarget]:
        """Capture ``targets`` at ``at`` where the chunk has none yet, in ``transaction``. Answers the chunk's captured
        targets — a repository captured earlier keeps its first value."""
        return self._targets.capture(chunk_id, [target.captured(at) for target in targets], transaction=transaction)
