"""Binds the garden delivery's :data:`CommitResolver` to the repository records.

Each call resolves the cited repository to its record, reveals the record's secret, and asks
the forge — nothing about a forge is held between calls, so a record edit or a secret replace
reaches the next one (``bzh:config-read-on-use``). A repository no record names, or whose secret
cannot be revealed, answers ``None``. A resolver is bound to one
project, and only the repositories that project links are candidates, read on each call too, so a
repository it does not link answers ``None`` the same way."""

from __future__ import annotations

import functools
from typing import Protocol

from blizzard.hub.domain.config.repositories import CommitOrigin, IReadRepositoryRecordRepository, resolve_repository
from blizzard.hub.domain.config.secrets import ISecretReader, SecretName
from blizzard.hub.domain.garden.delivery.validation import CommitResolution, CommitResolver
from blizzard.hub.domain.projects.links import IReadProjectLinks
from blizzard.hub.domain.projects.model import ProjectId


class ICommitForge(Protocol):
    def resolve(
        self, repo: str, commit: str, *, forge_url: str, owner: str, token: str | None
    ) -> CommitResolution | None: ...


class RepositoryCommitResolver:
    def __init__(
        self,
        *,
        repositories: IReadRepositoryRecordRepository,
        secrets: ISecretReader,
        forge: ICommitForge,
        links: IReadProjectLinks,
    ):
        self._repositories = repositories
        self._secrets = secrets
        self._forge = forge
        self._links = links

    def for_project(self, project_id: ProjectId) -> CommitResolver:
        """The resolver of ``project_id``'s deliveries: only the repositories it links."""
        return functools.partial(self._resolve, project_id)

    def _resolve(self, project_id: ProjectId, repo: str, commit: str) -> CommitResolution | None:
        """Never raises (``CommitResolver``'s own contract)."""
        try:
            row = resolve_repository(
                CommitOrigin.of(None, repo),
                self._repositories.list_all(include_retired=False),
                linked=frozenset(link.repository_id for link in self._links.repositories_of(project_id)),
            )
            if row is None:
                return None
            token = self._secrets.reveal(SecretName.parse(row.fields.secret_name)).expose()
            return self._forge.resolve(
                row.fields.repo, commit, forge_url=row.fields.forge_api_url, owner=row.fields.owner, token=token
            )
        except Exception:
            # A secret that cannot be revealed reads as no forge, like any other fault here.
            return None
