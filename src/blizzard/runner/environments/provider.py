"""The workspace-provider seam.

Allocates clean environments by opaque id, each with its working directory. Cleaning
happens on the *next* acquire, not on release; a provider may retain released folders."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.repo_ref import repo_identity
from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class WorkspaceRepo:
    """A repository a workspace holds: its name and clone URL."""

    name: str
    url: str


@domain_model
@dataclass(frozen=True)
class RepositoryTarget:
    """One repository a chunk's project links, with the branch the chunk starts from and lands into, as the hub names
    it. A worktree is the target's when its origin's ``owner/name`` is the target's (:meth:`names`). ``inherited`` when
    the branch is the repository's own rather than its project link's."""

    repository_id: str
    repository_slug: str
    forge_host: str
    owner: str
    repo: str
    base_branch: str
    inherited: bool

    @property
    def qualified(self) -> str:
        """``owner/repo`` — the identity a worktree's origin is matched by."""
        return f"{self.owner}/{self.repo}"

    def names(self, origin_url: str, name: str) -> bool:
        """Whether the worktree ``name`` whose origin is ``origin_url`` is this target's repository."""
        return repo_identity(origin_url, name) == self.qualified


def target_naming(targets: Sequence[RepositoryTarget], origin_url: str, name: str) -> RepositoryTarget | None:
    """The first of ``targets`` naming the worktree ``name`` whose origin is ``origin_url``, or ``None``."""
    return next((target for target in targets if target.names(origin_url, name)), None)


@domain_model
@dataclass(frozen=True)
class AcquiredEnvironment:
    """An acquired environment: its opaque id and its working directory.

    The ``workdir`` may not exist yet under a lazy binding."""

    environment_id: str
    workdir: str


@domain_model
@dataclass(frozen=True)
class RepoBinding:
    """Where repo ``name`` lives in an env, and what forge it pushes to.

    The layout is the provider's to declare (``bzh:pluggable-seams``) — never inferred.
    ``relpath`` is relative to the env's ``workdir``, the sole absolute anchor."""

    environment_id: str
    name: str
    relpath: str
    origin_url: str


class WorkspaceAcquisitionError(RuntimeError):
    """A provider could not satisfy an acquire (pool exhausted, git failure, …).

    All-or-nothing: a partial satisfaction is released rather than kept."""


class EnvironmentPreparationError(WorkspaceAcquisitionError):
    """A reset-on-acquire step failed while preparing an environment.

    Distinct from a plain refusal: the provider aborted mid-reset rather than hand
    over a half-reset environment, and names the failing ``step``."""

    def __init__(self, message: str, *, environment_id: str, step: str) -> None:
        super().__init__(message)
        self.environment_id = environment_id
        self.step = step


class TargetBranchMissingError(EnvironmentPreparationError):
    """A repository's target branch is absent on its origin: the environment is never started from another branch in
    its place."""

    def __init__(self, *, environment_id: str, repository: str, branch: str) -> None:
        super().__init__(
            f"repository {repository} has no branch {branch!r} on its origin",
            environment_id=environment_id,
            step="target-branch",
        )
        self.repository = repository
        self.branch = branch


class TargetUnmatchedError(EnvironmentPreparationError):
    """A repository whose branch is its project link's own matches no worktree of the environment: the environment is
    never started from the workspace's branch while delivery lands in the override."""

    def __init__(self, *, environment_id: str, repository: str, branch: str) -> None:
        super().__init__(
            f"repository {repository} targets branch {branch!r} but matches no worktree of the environment",
            environment_id=environment_id,
            step="target-unmatched",
        )
        self.repository = repository
        self.branch = branch


def refuse_unmatched_overrides(
    environment_id: str, targets: Sequence[RepositoryTarget], matched: Sequence[RepositoryTarget]
) -> None:
    """Raise :class:`TargetUnmatchedError` for the first override target of ``targets`` not in ``matched``; an
    inherited target matching nothing is the workspace's branch already."""
    for target in targets:
        if not target.inherited and target not in matched:
            raise TargetUnmatchedError(
                environment_id=environment_id, repository=target.qualified, branch=target.base_branch
            )


class TargetsUnsupportedError(EnvironmentPreparationError):
    """A provider that cannot prepare repositories at their targets was asked for a project link's own branch."""

    def __init__(self, *, environment_id: str, repository: str, branch: str) -> None:
        super().__init__(
            f"this workspace cannot prepare repository {repository} at branch {branch!r}",
            environment_id=environment_id,
            step="targets",
        )


def refuse_unhonored_targets(targets: Sequence[RepositoryTarget], *, environment_id: str) -> None:
    """The refusal of a provider that cannot honor targets: any target naming a project link's own branch raises
    :class:`TargetsUnsupportedError`; an inherited one is the repository's own branch and needs nothing of it."""
    override = next((target for target in targets if not target.inherited), None)
    if override is not None:
        raise TargetsUnsupportedError(
            environment_id=environment_id, repository=override.qualified, branch=override.base_branch
        )


class IWorkspaceProvider(Protocol):
    """The environment-allocation seam."""

    def acquire(
        self, chunk_id: str, count: int, held_ids: list[str], targets: Sequence[RepositoryTarget] = ()
    ) -> list[AcquiredEnvironment]:
        """Acquire ``count`` clean environments, excluding ``held_ids``, each repository worktree a target names on
        that target's branch and every other on the workspace's own.

        Returns the acquired ``(env id, workdir)`` pairs, or raises
        :class:`WorkspaceAcquisitionError` on refusal — never a partial set. A target branch absent on its origin
        raises :class:`TargetBranchMissingError`; a provider that does not :meth:`honors_targets` raises
        :class:`TargetsUnsupportedError` for an override target."""
        ...

    def honors_targets(self) -> bool:
        """Whether this binding prepares repository worktrees at the targets an acquire names."""
        ...

    def release(self, environment_id: str) -> None:
        """Release an environment. Idempotent; cleaning defers to next acquire."""
        ...

    def repos(self, environment_id: str) -> list[RepoBinding]:
        """Every repo worktree inside ``environment_id`` — the env's repo manifest.

        The authorization set *and* the origin-URL source: a repo absent from this list
        is outside the lease. An unknown or released env answers ``[]``, not a raise —
        an empty manifest authorizes nothing, the safe reading."""
        ...

    def spawn_root(self) -> str:
        """The directory every worker spawns in, or ``""`` when each worker spawns in
        its own environment's workdir. Stable for the provider's life."""
        ...

    def capacity(self) -> int:
        """How many environments this binding can hold at once."""
        ...

    def pool(self) -> tuple[str, ...]:
        """The standing environment ids, or ``()`` when environments are minted on acquire."""
        ...

    def environment_workdirs(self) -> dict[str, str]:
        """Each environment this provider lists now, mapped to its workdir — the workspace root
        joined with the environment."""
        ...
