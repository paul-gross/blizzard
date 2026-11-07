"""Current git commit declarations, independent of an action visit."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.repo_ref import pointer_key, repo_identity
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.config.repositories import CommitOrigin, RepositoryResolution, ResolvedRepository


class UnconvergedDeliveryError(RuntimeError):
    """Delivery was handed several distinct branches for one repo.

    Raised rather than tie-broken: merging the first invalidates the mergeability every
    other was checked against."""


@domain_model
@dataclass(frozen=True)
class GitCommits:
    """A chunk's ``git_commit`` artifacts resolved to one row per repository **identity**.

    A rewritten branch supersedes its orphaned pointer, but the same name under two owners
    or on two forge hosts stays two repositories."""

    rows: tuple[StoredArtifact, ...]

    @classmethod
    def of(cls, artifacts: Iterable[StoredArtifact]) -> GitCommits:
        """Newest epoch wins; a tie at one epoch resolves to the later row unless the two
        name different branches, which raises :class:`UnconvergedDeliveryError`."""
        latest: dict[str, StoredArtifact] = {}
        for row in artifacts:
            if row.kind is not ArtifactKind.GIT_COMMIT:
                continue
            identity = cls._identity(row)
            current = latest.get(identity)
            if current is None or row.epoch > current.epoch:
                latest[identity] = row
            elif row.epoch == current.epoch and row.data != current.data:
                raise UnconvergedDeliveryError(
                    f"repo {identity!r} has two different branches declared at epoch {row.epoch} "
                    f"({current.data!r} and {row.data!r}) — the environments' work was never "
                    f"rolled up into one branch per repo, so there is no single thing to deliver"
                )
            elif row.epoch == current.epoch:
                latest[identity] = row  # identical re-declaration: a correction, not a second unit
        return cls(tuple(latest.values()))

    def origins(self) -> list[CommitOrigin]:
        """Each row's repository as its origin names it, in :attr:`rows` order."""
        return [CommitOrigin.of(row.forge, row.repo or row.name) for row in self.rows]

    def payload(
        self, qualified: tuple[str, ...] = (), resolution: RepositoryResolution | None = None
    ) -> list[dict[str, str | None]]:
        """The ``run:`` env's commit list: ``repo`` is the ``owner/repo`` of the repository record the
        row resolved to (``qualified``, in :attr:`rows` order); unresolved, the row's own identity.
        Given a ``resolution`` with one target per row, each entry also carries its own record's target:
        ``forge_url`` — its forge's API origin — ``owner``, ``base_branch`` and the ``record`` id."""
        targets: tuple[ResolvedRepository, ...] = () if resolution is None else resolution.targets
        entries: list[dict[str, str | None]] = []
        for index, row in enumerate(self.rows):
            entry: dict[str, str | None] = {
                "repo": qualified[index] if qualified else self._qualified(row),
                "branch": row.data.partition(":")[0],
                "commit": row.data.partition(":")[2],
            }
            if targets:
                target = targets[index]
                entry |= {
                    "forge_url": resolution.forge_of(target).fields.api_base if resolution else None,
                    "owner": target.owner,
                    "base_branch": target.base_branch,
                    "record": target.record,
                }
            entries.append(entry)
        return entries

    @staticmethod
    def _identity(row: StoredArtifact) -> str:
        """The host-qualified grouping key, from the stored ``forge`` + ``repo``, never the runner-sent name."""
        return pointer_key(row.forge, row.repo or row.name)

    @staticmethod
    def _qualified(row: StoredArtifact) -> str:
        return repo_identity(row.forge, row.repo or row.name)
