"""A lease's declared git commits, and confirming each against the origin that owns it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.repo_ref import repo_identity
from blizzard.runner.environments.internal.subprocess_worktree_git import WorktreeGitError
from blizzard.runner.environments.provider import IWorkspaceProvider
from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.environments.worktree import IWorktreeGit
from blizzard.runner.hub.outbound import OutboundContext, OutboundFacts, OutboundStores
from blizzard.runner.leases import Lease
from blizzard.runner.lifecycle.judgement.git_commit_declaration import (
    GitCommitDeclaration,
    IReadGitCommitDeclarationRepository,
)
from blizzard.wire.completion import SubmittedArtifact

Key = tuple[str, str]


class GitCommitsStores(OutboundStores, Protocol):
    @property
    def git_commit_declarations(self) -> IReadGitCommitDeclarationRepository: ...


class GitCommitsContext(OutboundContext, Protocol):
    @property
    def stores(self) -> GitCommitsStores: ...
    @property
    def provider(self) -> IWorkspaceProvider: ...
    @property
    def worktree_git(self) -> IWorktreeGit: ...


@dataclass
class DeclaredCommits:
    """This lease's declared git commits, confirmed **read-only** against the
    origin each declaring environment's manifest names.

    Never mutates git or infers branches from residue. Invalid declarations produce
    ``command-failed`` events."""

    ctx: GitCommitsContext
    lease: Lease
    bindings: list[EnvBinding]
    _resolved: dict[Key, GitCommitDeclaration] = field(default_factory=dict)
    _confirmed: dict[Key, SubmittedArtifact] = field(default_factory=dict)
    _submitted: dict[str, list[SubmittedArtifact]] = field(default_factory=dict)

    def verify(self) -> list[SubmittedArtifact]:
        """Confirm every declaration this instance has not already resolved, in declaration
        order, then converge the lease's whole confirmed set by repository identity.

        Pointers agreeing on branch and commit submit as one; disagreeing pointers are
        all submitted and reported. Return only groups new or changed since the last call."""
        origins = self._origins()
        changed = False
        for key, declared in self.ctx.stores.git_commit_declarations.git_commit_declarations_for_lease(
            self.lease.lease_id
        ).items():
            if self._resolved.get(key) == declared:
                continue
            self._resolved[key] = declared
            self._confirmed.pop(key, None)
            artifact = self._confirm(key, declared, origins)
            if artifact is not None:
                self._confirmed[key] = artifact
                changed = True
        return self._converge() if changed else []

    def _converge(self) -> list[SubmittedArtifact]:
        groups: dict[str, list[tuple[Key, SubmittedArtifact]]] = {}
        for key, artifact in self._confirmed.items():
            groups.setdefault(artifact.name, []).append((key, artifact))
        artifacts: list[SubmittedArtifact] = []
        for identity, members in groups.items():
            pointers = {(a.branch_name, a.commit_hash): a for _, a in members}
            group = list(pointers.values())
            if self._submitted.get(identity) == group:
                continue
            self._submitted[identity] = group
            if len(group) > 1:
                self._report(
                    command=f"converge commit pointers for repository {identity!r}",
                    stderr_tail="environments declare different pointers: "
                    + "; ".join(f"{env_id!r} -> {a.branch_name}@{a.commit_hash}" for (env_id, _), a in members),
                )
            artifacts.extend(group)
        return artifacts

    def _confirm(self, key: Key, declared: GitCommitDeclaration, origins: dict[Key, str]) -> SubmittedArtifact | None:
        env_id, repo = key
        origin_url = origins.get(key)
        if origin_url is None:
            # Reaching here means the manifest changed under the lease, not a worker typo.
            # An unresolvable origin means this commit cannot be delivered — say so.
            self._report(
                command=f"resolve origin for --repo {repo!r} in environment {env_id!r}",
                stderr_tail=(
                    f"environment {env_id!r} no longer lists repo {repo!r}; "
                    f"it lists {sorted(name for (env, name) in origins if env == env_id)}"
                ),
            )
            return None
        command = f"git ls-remote {origin_url} {declared.branch} (--repo {repo!r}, --env {env_id!r})"
        try:
            verified = self.ctx.worktree_git.verify(origin_url, declared.branch, declared.commit)
        except WorktreeGitError as exc:
            self._report(command=command, stderr_tail=str(exc))
            return None
        if not verified:
            self._report(
                command=command,
                stderr_tail=(
                    f"declared commit {declared.commit} is not what branch {declared.branch!r} "
                    f"points at on {origin_url} — push the branch (or re-declare the sha "
                    f"`git rev-parse HEAD` actually produced) and declare it again"
                ),
            )
            return None
        return SubmittedArtifact(
            name=repo_identity(origin_url, repo),
            kind=ArtifactKind.GIT_COMMIT,
            forge=origin_url,
            repo=repo,
            branch_name=declared.branch,
            commit_hash=declared.commit,
        )

    def _origins(self) -> dict[Key, str]:
        """``{(environment_id, repo): origin_url}`` across every bound environment.

        The provider is the authority on both which repos an env holds and where each pushes,
        so this is a lookup, never a path guessed from a workdir or a cwd."""
        origins: dict[Key, str] = {}
        for binding in self.bindings:
            for repo in self.ctx.provider.repos(binding.environment_id):
                origins[(binding.environment_id, repo.name)] = repo.origin_url
        return origins

    def _report(self, *, command: str, stderr_tail: str) -> None:
        OutboundFacts(self.ctx).command_failed(
            chunk_id=self.lease.chunk_id,
            lease_id=self.lease.lease_id,
            node_name=self.lease.node_name,
            command=command,
            stderr_tail=stderr_tail,
        )
