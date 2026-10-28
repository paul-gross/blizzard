"""What a hub action's pass is handed: a frozen read of the visit, taken before the pass begins.

Every field is immutable all the way down — frozen dataclasses, tuples, frozensets, and
``MappingProxyType`` — and the visit carries projections, never the domain's ``Chunk``, ``Graph``,
or ``Node``, whose lists would make it mutable through them (``bzh:hub-action-purity``)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.repo_ref import pointer_key, repo_identity
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.config.forges import ConfiguredForge
from blizzard.hub.domain.config.repositories import (
    CommitOrigin,
    ConfiguredRepository,
    RepositoryOutcome,
    RepositoryResolution,
    ResolvedRepository,
    resolve_chunk_repositories,
)
from blizzard.hub.domain.projects.model import ProjectId


@domain_model
@dataclass(frozen=True)
class LandingProject:
    """The project a chunk lands for, read as a hub node visit starts: its id, its slug, and the
    ids of the repository records it links. A chunk record carrying no project links nothing."""

    project_id: str = ""
    slug: str = ""
    linked: frozenset[str] = frozenset()


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


@domain_model
@dataclass(frozen=True)
class VisitRepositories:
    """A visit's commits and the repository records they resolved to, once nothing refused them."""

    git_commits: GitCommits
    resolution: RepositoryResolution


@domain_model
@dataclass(frozen=True)
class VisitRefusal:
    """Why a visit cannot run, named before any pass: the hub-log ``step`` it is refused under and its ``detail``.
    ``resolution`` is the resolution that refused, ``None`` when the commits never converged to be resolved."""

    step: str
    detail: str
    resolution: RepositoryResolution | None = None


#: The resolution outcomes a visit is refused under, by their own hub-log step.
_REFUSED_OUTCOMES = frozenset({RepositoryOutcome.UNRESOLVED, RepositoryOutcome.NOT_LINKED})


def resolve_visit(
    artifacts: Iterable[StoredArtifact],
    *,
    project: LandingProject,
    minted_at: datetime,
    repositories: list[ConfiguredRepository],
    forges: Iterable[ConfiguredForge],
    lands: bool,
) -> VisitRepositories | VisitRefusal:
    """Resolve a visit's repositories, or name why it is refused: commits that never converged, a resolution that
    refused, or — only for an action that ``lands``, since only a landing writes ``merged/<repo>`` markers — one
    ``owner/repo`` resolved to two records. The one home of these refusals: every reader resolving a visit asks here."""
    try:
        git_commits = GitCommits.of(artifacts)
    except UnconvergedDeliveryError as exc:
        return VisitRefusal("unconverged-delivery", str(exc))
    resolution = resolve_chunk_repositories(
        git_commits.origins(),
        repositories,
        forges,
        minted_at=minted_at,
        linked=project.linked,
        project=project.slug,
    )
    if resolution.outcome in _REFUSED_OUTCOMES:
        return VisitRefusal(resolution.outcome.value, resolution.detail, resolution)
    if lands and (ambiguity := _ambiguity(resolution)) is not None:
        return VisitRefusal("repository-ambiguous", ambiguity, resolution)
    return VisitRepositories(git_commits, resolution)


def _ambiguity(resolution: RepositoryResolution) -> str | None:
    """Why a resolution holding one ``owner/repo`` for two records is refused: both landings would write the one
    ``merged/<repo>`` marker. ``None`` when every ``owner/repo`` is one record's."""
    held: dict[str, ResolvedRepository] = {}
    for repo, target in zip(resolution.qualified, resolution.targets, strict=True):
        first = held.setdefault(repo, target)
        if first.record != target.record:
            return (
                f"the repo {repo!r} resolves to two repository records, and so to one marker name: "
                f"record {first.record!r} ({resolution.forge_of(first).fields.api_base}) and record "
                f"{target.record!r} ({resolution.forge_of(target).fields.api_base})"
            )
    return None


@domain_model
@dataclass(frozen=True)
class VisitChunk:
    """The chunk a pass visits, as far as an action may read it."""

    chunk_id: str
    project_id: ProjectId | None
    minted_at: datetime
    work_refs: tuple[WorkRef, ...]


@domain_model
@dataclass(frozen=True)
class VisitNode:
    """The hub node a pass takes: its action, the action's ``with:`` arguments, and whether its graph
    expects git commits."""

    node_id: str
    name: str
    action: str
    action_args: Mapping[str, tuple[str, ...]]
    expects_git_commits: bool


@domain_model
@dataclass(frozen=True)
class Visit:
    """Everything a pass reads, fixed as the visit starts. ``markers`` are the artifacts recorded at this
    node across every epoch, so a re-visit sees what earlier visits wrote; ``work_items`` are one
    ``{label, reference}`` mapping per work ref."""

    chunk: VisitChunk
    node: VisitNode
    epoch: int
    artifacts: tuple[StoredArtifact, ...]
    markers: tuple[StoredArtifact, ...]
    git_commits: GitCommits
    resolution: RepositoryResolution
    work_items: tuple[Mapping[str, str | None], ...]
    feature_title: str | None
    public_url: str | None
    project: LandingProject

    def marker_names(self) -> frozenset[str]:
        """The names of the markers recorded at this node, in any epoch."""
        return frozenset(row.name for row in self.markers)
