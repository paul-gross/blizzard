"""The garden delivery's commit check, asked of the cited repository's forge through :class:`IForgeRefs`.

The check runs in the effect writer's thread while every adapter lives on the action loop, so the read is submitted
there and waited on for at most :data:`COMMIT_CHECK_SECONDS`, the future cancelled when the wait runs out. Every case
where the forge cannot be asked or cannot say answers ``None``, degrading the commit to a well-formedness check."""

from __future__ import annotations

import concurrent.futures
import functools
from typing import Protocol

from blizzard.hub.delivery.action_loop import ActionLoop
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.forges import ConfiguredForge, IReadForgeRepository
from blizzard.hub.domain.config.repositories import (
    CommitOrigin,
    IReadRepositoryRecordRepository,
    RepositoryId,
    resolve_repository,
)
from blizzard.hub.domain.garden.delivery.validation import CommitResolution, CommitResolver
from blizzard.hub.domain.projects.links import IReadProjectLinks
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.forge.landing import IForgeRefs

#: The longest the writer waits on the forge for one cited commit.
COMMIT_CHECK_SECONDS = 10.0


class IForgeRefsSource(Protocol):
    """Which capabilities a configured forge offers, and a repository's refs."""

    def offered(self, forge: ConfiguredForge, repository: RepositoryId | None = None) -> frozenset[Capability]: ...

    def refs(self, repository: RepositoryId) -> IForgeRefs: ...


class SeamCommitResolver:
    def __init__(
        self,
        *,
        repositories: IReadRepositoryRecordRepository,
        forge_records: IReadForgeRepository,
        links: IReadProjectLinks,
        forges: IForgeRefsSource,
        loop: ActionLoop,
        timeout: float = COMMIT_CHECK_SECONDS,
    ) -> None:
        self._repositories = repositories
        self._forge_records = forge_records
        self._links = links
        self._forges = forges
        self._loop = loop
        self._timeout = timeout

    def for_project(self, project_id: ProjectId) -> CommitResolver:
        """The resolver of ``project_id``'s deliveries: only the repositories it links."""
        return functools.partial(self._resolve, project_id)

    def _resolve(self, project_id: ProjectId, repo: str, commit: str) -> CommitResolution | None:
        """Never raises (``CommitResolver``'s own contract)."""
        try:
            forges = {forge.forge_id: forge for forge in self._forge_records.list_all(include_retired=True)}
            row = resolve_repository(
                CommitOrigin.of(None, repo),
                self._repositories.list_all(include_retired=False),
                forges.values(),
                linked=frozenset(link.repository_id for link in self._links.repositories_of(project_id)),
            )
            if row is None:
                return None
            if Capability.COMMITS_READ not in self._forges.offered(forges[row.fields.forge_id], row.repository_id):
                return None
            refs = self._forges.refs(row.repository_id)
            future = self._loop.submit(refs.commit_exists(commit))
            try:
                presence = future.result(timeout=self._timeout)
            except concurrent.futures.TimeoutError:
                future.cancel()
                return None
            return CommitResolution(exists=presence.exists, authored_at=presence.authored_at)
        except Exception:
            # An unrevealable secret, an unavailable forge, and a repository the credential cannot see all read as no
            # forge, like any other fault here.
            return None
