"""What a hub action's pass is handed: a frozen read of the visit, taken before the pass begins.

Every field is immutable all the way down — frozen dataclasses, tuples, frozensets, and
``MappingProxyType`` — and the visit carries projections, never the domain's ``Chunk``, ``Graph``,
or ``Node``, whose lists would make it mutable through them (``bzh:hub-action-purity``)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.repo_ref import pointer_key, repo_identity
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.chunk.no_code import no_code_marker, no_code_repos
from blizzard.hub.domain.config.forges import ConfiguredForge
from blizzard.hub.domain.config.landing_policies import LandingPolicy, landing_sort_key
from blizzard.hub.domain.config.repositories import (
    CommitOrigin,
    ConfiguredRepository,
    RepositoryId,
    RepositoryOutcome,
    RepositoryResolution,
    ResolvedRepository,
    resolve_chunk_repositories,
)
from blizzard.hub.domain.graph.actions import ActionSpec
from blizzard.hub.domain.projects.links import IReadProjectLinks
from blizzard.hub.domain.projects.model import IReadProjectRepository, ProjectId


@domain_model
@dataclass(frozen=True)
class LandingProject:
    """The project a chunk lands for, read as a hub node visit starts: its id, its slug, the
    ids of the repository records it links, and each linked record's current position in its landing
    order — ``None`` when unranked. A chunk record carrying no project links nothing."""

    project_id: str = ""
    slug: str = ""
    linked: frozenset[str] = frozenset()
    positions: Mapping[str, float | None] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def read(
        cls, project_id: ProjectId | None, *, projects: IReadProjectRepository, links: IReadProjectLinks
    ) -> LandingProject:
        """The chunk's project and the repository records it links, read now."""
        if project_id is None:
            return cls()
        project = projects.get(project_id)
        positions: dict[str, float | None] = {
            link.repository_id: link.position for link in links.repositories_of(project_id)
        }
        return cls(
            project_id=project_id,
            slug=project.slug if project is not None else project_id,
            linked=frozenset(positions),
            positions=MappingProxyType(positions),
        )


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
    """A visit's action, its commits, and the repository records they resolved to, once nothing refused them."""

    spec: ActionSpec
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
    action: str | None,
    spec: ActionSpec | None,
    project: LandingProject,
    minted_at: datetime,
    repositories: list[ConfiguredRepository],
    forges: Iterable[ConfiguredForge],
) -> VisitRepositories | VisitRefusal:
    """Resolve a visit's repositories, or name why it is refused: its ``action`` having no ``spec`` this hub runs,
    commits that never converged, a resolution that refused, or — only for an action that ``lands``, since only a
    landing writes ``merged/<repo>`` markers — one ``owner/repo`` resolved to two records. The one home of these
    refusals: every reader resolving a visit asks here."""
    if spec is None:
        return VisitRefusal("action-unavailable", f"no hub action `{action}`")
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
    if spec.lands and (ambiguity := _ambiguity(resolution)) is not None:
        return VisitRefusal("repository-ambiguous", ambiguity, resolution)
    return VisitRepositories(spec, git_commits, resolution)


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
    ``{label, reference}`` mapping per work ref. ``first_turn_at`` is when the visit's first turn started, across
    every generation of its submissions; ``now`` is the driver's clock as it built the visit."""

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
    first_turn_at: datetime
    now: datetime

    def marker_names(self) -> frozenset[str]:
        """The names of the markers recorded at this node, in any epoch."""
        return frozenset(row.name for row in self.markers)

    def no_code_repos(self) -> frozenset[str]:
        """Current declarations resolved without a merge at this node."""
        return no_code_repos(self.markers, self.git_commits.payload(self.resolution.qualified, self.resolution))

    def no_code_marker(self, repo: str) -> tuple[str, str]:
        """The marker for this visit's resolved declaration of ``repo``."""
        declaration = next(
            row for row in self.git_commits.payload(self.resolution.qualified, self.resolution) if row["repo"] == repo
        )
        return no_code_marker(declaration)
    def parked_watch(self) -> RepositoryId | None:
        """The first unlanded parked repository with an existing change, unless it needs a fresh review."""
        names = self.marker_names()
        pairs = sorted(
            zip(self.resolution.targets, self.resolution.qualified, strict=True),
            key=lambda pair: landing_sort_key(pair[0].landing_position, pair[0].slug),
        )
        for target, repo in pairs:
            if f"merged/{repo}" in names:
                continue
            if target.landing_policy is not LandingPolicy.PARK:
                return None
            prefix = f"delivery-pr/{repo}/"
            marker = next((row for row in reversed(self.markers) if row.name.startswith(prefix)), None)
            if marker is None or any(
                row.name == f"park-reopen/{repo}/{marker.name.rsplit('/', 1)[1]}" and row.epoch == self.epoch
                for row in self.markers
            ):
                return None
            return RepositoryId(target.record)
        return None
