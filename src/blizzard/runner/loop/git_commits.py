"""A lease's declared git commits, and confirming each against the origin that owns it."""

from __future__ import annotations

from dataclasses import dataclass, field

from blizzard.hub.domain.artifacts import ArtifactKind
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.internal.subprocess_worktree_git import WorktreeGitError
from blizzard.runner.loop.outbound import OutboundFacts
from blizzard.runner.store.repository import EnvBindingRecord, GitCommitDeclarationRecord, LeaseRecord
from blizzard.wire.completion import SubmittedArtifact

Key = tuple[str, str]


@dataclass
class DeclaredCommits:
    """This lease's declared git commits (issue #143), confirmed **read-only** against the
    origin each declaring environment's manifest names.

    Never mutates git and never infers a branch off residue. A declaration that does not
    verify is reported as a ``command-failed`` event, never silently dropped."""

    ctx: LoopContext
    lease: LeaseRecord
    bindings: list[EnvBindingRecord]
    _resolved: dict[Key, GitCommitDeclarationRecord] = field(default_factory=dict)

    def verify(self) -> list[SubmittedArtifact]:
        """Confirm every declaration this instance has not already resolved, in declaration
        order. Spans **every** bound environment, since the key carries the env."""
        origins = self._origins()
        artifacts: list[SubmittedArtifact] = []
        for key, declared in self.ctx.store.git_commit_declarations_for_lease(self.lease.lease_id).items():
            if self._resolved.get(key) == declared:
                continue
            self._resolved[key] = declared
            artifact = self._confirm(key, declared, origins)
            if artifact is not None:
                artifacts.append(artifact)
        return artifacts

    def _confirm(
        self, key: Key, declared: GitCommitDeclarationRecord, origins: dict[Key, str]
    ) -> SubmittedArtifact | None:
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
            name=repo,
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
