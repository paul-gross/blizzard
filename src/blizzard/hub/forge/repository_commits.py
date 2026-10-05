"""Binds the garden delivery's :data:`CommitResolver` to the repository records.

Each call resolves the cited repository to its record, reveals the record's secret, and asks
the forge — nothing about a forge is held between calls, so a record edit or a secret replace
reaches the next one (``bzh:config-read-on-use``). A repository no record names, or whose secret
cannot be revealed, answers ``None``: today's "no forge configured"."""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.domain.config.repositories import CommitOrigin, IReadRepositoryRecordRepository, resolve_repository
from blizzard.hub.domain.config.secrets import ISecretReader, SecretName, SecretNotFound, SecretRetired
from blizzard.hub.domain.garden.delivery.validation import CommitResolution


class ICommitForge(Protocol):
    def resolve(
        self, repo: str, commit: str, *, forge_url: str, owner: str, token: str | None
    ) -> CommitResolution | None: ...


class RepositoryCommitResolver:
    def __init__(self, *, repositories: IReadRepositoryRecordRepository, secrets: ISecretReader, forge: ICommitForge):
        self._repositories = repositories
        self._secrets = secrets
        self._forge = forge

    def resolve(self, repo: str, commit: str) -> CommitResolution | None:
        """Never raises (``CommitResolver``'s own contract)."""
        try:
            row = resolve_repository(CommitOrigin.of(None, repo), self._repositories.list_all(include_retired=False))
            if row is None:
                return None
            token = self._secrets.reveal(SecretName.parse(row.fields.secret_name)).expose()
            return self._forge.resolve(
                row.fields.repo, commit, forge_url=row.fields.forge_api_url, owner=row.fields.owner, token=token
            )
        except (SecretNotFound, SecretRetired):
            return None
        except Exception:
            return None
