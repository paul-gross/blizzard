"""What a hub action's pass is handed: a frozen read of the visit, taken before the pass begins.

Every field is immutable all the way down — frozen dataclasses, tuples, frozensets, and
``MappingProxyType`` — and the visit carries projections, never the domain's ``Chunk``, ``Graph``,
or ``Node``, whose lists would make it mutable through them (``bzh:hub-action-purity``)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.actions.declarations import GitCommits, UnconvergedDeliveryError
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import LandedRepos, WorkRef
from blizzard.hub.domain.chunk.no_code import finished_repos, no_code_marker, no_code_repos
from blizzard.hub.domain.chunk.pr_markers import is_reopened, latest_pr_marker
from blizzard.hub.domain.config.forges import ConfiguredForge
from blizzard.hub.domain.config.landing_policies import POLICY_FACTS, landing_sort_key
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    RepositoryId,
    RepositoryOutcome,
    RepositoryResolution,
    ResolvedRepository,
    resolve_chunk_repositories,
)
from blizzard.hub.domain.graph.actions import ActionSpec
from blizzard.hub.domain.projects.model import ProjectId


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

    def landed_repos(self) -> frozenset[str]:
        """The repos landed anywhere in the chunk's delivery — a non-blank ``merged/<repo>`` at any node."""
        return LandedRepos.of(self.artifacts).names

    def no_code_repos(self) -> frozenset[str]:
        """Current declarations resolved without a merge at this node."""
        return no_code_repos(self.markers, self.git_commits.payload(self.resolution.qualified, self.resolution))

    def finished_repos(self) -> frozenset[str]:
        """Repos landed anywhere or resolved without code at this node."""
        return finished_repos(
            self.artifacts, self.markers, self.git_commits.payload(self.resolution.qualified, self.resolution)
        )

    def no_code_marker(self, repo: str) -> tuple[str, str]:
        """The marker for this visit's resolved declaration of ``repo``."""
        declaration = next(
            row for row in self.git_commits.payload(self.resolution.qualified, self.resolution) if row["repo"] == repo
        )
        return no_code_marker(declaration)

    def watched_repo(self) -> RepositoryId | None:
        """The first unfinished parked repository with an existing change, unless it needs a fresh review."""
        finished = self.finished_repos()
        pairs = sorted(
            zip(self.resolution.targets, self.resolution.qualified, strict=True),
            key=lambda pair: landing_sort_key(pair[0].landing_position, pair[0].slug),
        )
        for target, repo in pairs:
            if repo in finished:
                continue
            if not POLICY_FACTS[target.landing_policy].watches:
                return None
            marker = latest_pr_marker(self.markers, repo)
            if marker is None or is_reopened(self.markers, repo, marker, self.epoch):
                return None
            return RepositoryId(target.record)
        return None
