"""Commit-pointer validation — refuse a submitted ``git_commit`` artifact that does not
name a repository, a branch, and a full object name. Every other artifact kind is stored as
submitted."""

from __future__ import annotations

import re
from dataclasses import dataclass

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.wire.completion import SubmittedArtifact

# A full lowercase object name: SHA-1 (40) or SHA-256 (64).
_FULL_OBJECT_NAME = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


@dataclass(frozen=True)
class CommitPointerPolicy:
    """One submission's ``git_commit`` artifacts judged for a complete pointer —
    already-loaded values only (``bzh:domain-takes-objects``)."""

    artifacts: list[SubmittedArtifact]

    def rejection(self) -> str | None:
        """A failure detail naming the artifact and the field at fault, or ``None`` when
        every commit pointer is well formed."""
        for artifact in self.artifacts:
            if artifact.kind is not ArtifactKind.GIT_COMMIT:
                continue
            fault = self._fault(artifact)
            if fault is not None:
                return f"git_commit artifact `{artifact.name}`: {fault}"
        return None

    @staticmethod
    def _fault(artifact: SubmittedArtifact) -> str | None:
        if not (artifact.repo or "").strip():
            return "`repo` is required"
        branch = artifact.branch_name or ""
        if not branch.strip():
            return "`branch_name` is required"
        if ":" in branch:
            return "`branch_name` must not contain `:`"
        if not _FULL_OBJECT_NAME.fullmatch(artifact.commit_hash or ""):
            return "`commit_hash` must be a full lowercase 40- or 64-hex object name"
        return None
