"""The landing seam: the roles the hub acts on one repository's forge through, and the values that cross them.

An adapter is built for one repository — its forge's addresses, its ``owner/repo``, and its revealed credential — so no
operation names a forge, an owner, or a token. The roles stay apart (``bzh:seam-size-ceiling``): a caller is typed
against the ones it calls. This module imports no HTTP client, so an action may name it (``bzh:hub-action-purity``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.repositories import RepositoryId


class ComparisonStatus(StrEnum):
    IDENTICAL = "identical"
    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"


class ChangeListing(StrEnum):
    """Which changes :meth:`IForgeChanges.changes` lists; ``closed`` includes merged."""

    OPEN = "open"
    CLOSED = "closed"


class ChangeState(StrEnum):
    OPEN = "open"
    MERGED = "merged"
    CLOSED = "closed"


class Mergeability(StrEnum):
    """``pending`` while the forge has not decided, or answers a state the hub does not merge through."""

    MERGEABLE = "mergeable"
    BEHIND = "behind"
    CONFLICTING = "conflicting"
    BLOCKED = "blocked"
    DRAFT = "draft"
    PENDING = "pending"


class UpdateOutcome(StrEnum):
    """``conflict`` when the forge could not bring the change up to its base without a person; ``refused`` for any
    other refusal."""

    ACCEPTED = "accepted"
    CONFLICT = "conflict"
    REFUSED = "refused"


class UpdateMethod(StrEnum):
    MERGE = "merge"
    REBASE = "rebase"


class MergeMethod(StrEnum):
    MERGE = "merge"
    SQUASH = "squash"
    REBASE = "rebase"


class CheckOutcome(StrEnum):
    """The only part of a check ever routed on; ``neutral`` counts as green wherever ``passed`` does."""

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    NEUTRAL = "neutral"


class ForgeObject(StrEnum):
    """What a :class:`ForgeNotFound` says does not exist."""

    REPOSITORY = "repository"
    BRANCH = "branch"
    CHANGE = "change"
    CHECK = "check"
    REVISION = "revision"


@domain_model
@dataclass(frozen=True)
class CommitSummary:
    revision: str
    parents: tuple[str, ...]


@domain_model
@dataclass(frozen=True)
class FileChange:
    """One file a comparison changed; ``patch`` is ``None`` when the forge gave none."""

    path: str
    previous_path: str | None
    status: str
    blob: str | None
    patch: str | None
    additions: int
    deletions: int


@domain_model
@dataclass(frozen=True)
class Comparison:
    """``base...head``: ``commits`` are the head side's, ``commits_truncated`` says there are fewer of them than the
    head is ``ahead``, and ``files_truncated`` says the forge cut the file list short."""

    status: ComparisonStatus
    ahead: int
    behind: int
    merge_base: str | None
    commits: tuple[CommitSummary, ...]
    commits_truncated: bool
    files: tuple[FileChange, ...]
    files_truncated: bool


@domain_model
@dataclass(frozen=True)
class CommitPresence:
    exists: bool
    authored_at: datetime | None = None


@domain_model
@dataclass(frozen=True)
class Change:
    """A pull or merge request. ``base_revision`` is the base revision the forge last compared it against;
    ``merged_revision``, set once ``state`` is merged, is the commit the merge put on the base — never the head."""

    number: int
    head: str
    head_revision: str
    base: str
    base_revision: str
    state: ChangeState
    mergeability: Mergeability
    web_url: str
    merged_revision: str | None
    closed_at: datetime | None = None


@domain_model
@dataclass(frozen=True)
class Update:
    outcome: UpdateOutcome
    method: UpdateMethod


@domain_model
@dataclass(frozen=True)
class Check:
    """One check on a revision. ``detail`` is the forge's own word for the result and ``details_url`` the page of its
    log, both carried for display only."""

    id: str
    name: str
    outcome: CheckOutcome
    detail: str
    details_url: str | None


class ForgeError(Exception):
    """A forge call's answer the hub cannot take as a value."""


class ForgeNotFound(ForgeError):
    """Something the call named does not exist, or this credential cannot reach it."""

    def __init__(self, what: ForgeObject, message: str = "") -> None:
        super().__init__(message or f"{what} not found")
        self.what = what


class ForgeRejected(ForgeError):
    """A refusal the forge explained: ``reason`` is one of the named reasons, or the forge's own message."""

    AUTH = "auth"
    CONFLICT = "conflict"
    STALE = "stale"
    PROTECTED = "protected"
    METHOD = "method"

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(f"{reason}: {message}" if message and message != reason else reason)
        self.reason = reason


class ForgeUnavailable(ForgeError):
    """No answer the hub can act on: a refused connection, a timeout, a 5xx."""


class ForgeRateLimited(ForgeUnavailable):
    """An unavailable forge whose answer says when to come back."""

    def __init__(self, resume_at: datetime, message: str = "") -> None:
        super().__init__(message or f"rate limited until {resume_at}")
        self.resume_at = resume_at


class ForgeCapabilityMissing(Exception):
    """The repository's forge does not offer what the operation needs; refused before the call reaches the adapter."""

    def __init__(self, capability: Capability) -> None:
        super().__init__(f"the forge does not offer {capability}")
        self.capability = capability


#: The capability each operation needs, by operation name; ``merge`` is keyed ``merge.<method>``.
OPERATION_CAPABILITY: Mapping[str, Capability] = {
    "branch_tip": Capability.REFS_READ,
    "compare": Capability.COMPARE,
    "commit_exists": Capability.COMMITS_READ,
    "changes": Capability.CHANGES_READ,
    "change": Capability.CHANGES_READ,
    "open_change": Capability.CHANGES_OPEN,
    "update_from_base": Capability.CHANGES_UPDATE_FROM_BASE,
    f"merge.{MergeMethod.MERGE}": Capability.CHANGES_MERGE_MERGE,
    f"merge.{MergeMethod.SQUASH}": Capability.CHANGES_MERGE_SQUASH,
    f"merge.{MergeMethod.REBASE}": Capability.CHANGES_MERGE_REBASE,
    "checks": Capability.CHECKS_READ,
    "rerun_check": Capability.CHECKS_RERUN,
    "advance_ref": Capability.REFS_ADVANCE,
    "merge_into": Capability.BRANCHES_MERGE,
    "create_ref": Capability.REFS_CREATE,
    "delete_ref": Capability.REFS_DELETE,
    "recently_closed": Capability.CHANGES_READ,
    "change_checks": Capability.CHECKS_READ,
}


class IForgeRefs(Protocol):
    """Branches, revisions, and commits."""

    async def branch_tip(self, branch: str) -> str | None:
        """The revision ``branch`` points at, or ``None`` when it does not exist."""
        ...

    async def compare(self, base: str, head: str) -> Comparison: ...

    async def commit_exists(self, revision: str) -> CommitPresence:
        """:class:`ForgeUnavailable` when the forge could say neither."""
        ...

    async def advance_ref(self, branch: str, expected: str, revision: str) -> None:
        """Fast-forward ``branch`` from ``expected`` to ``revision``; ``ForgeRejected("stale")`` when it was not at
        ``expected``, ``ForgeRejected("protected")`` when a rule of the repository forbids it."""
        ...

    async def merge_into(self, branch: str, revision: str, message: str) -> str | None:
        """The merge commit on ``branch`` bringing ``revision`` in, ``None`` when ``branch`` already holds it;
        ``ForgeRejected("conflict")`` on a conflict, ``ForgeRejected("protected")`` when a rule forbids it."""
        ...

    async def create_ref(self, branch: str, revision: str) -> None:
        """Create ``branch`` at ``revision``; one already there is no error, one at another revision is
        ``ForgeRejected("stale")``."""
        ...

    async def delete_ref(self, branch: str) -> None:
        """Delete ``branch``; one already gone is no error."""
        ...


class IForgeChanges(Protocol):
    """The changes — pull or merge requests — that carry work into a branch."""

    async def changes(self, state: ChangeListing, base: str, head: str | None = None) -> tuple[Change, ...]:
        """The changes into ``base`` (from ``head`` when named), newest first, bounded and paged."""
        ...

    async def recently_closed(self, base: str, limit: int) -> tuple[Change, ...]:
        """The changes into ``base`` closed or merged, most recently updated first, at most ``limit``; one page."""
        ...

    async def open_change(self, head: str, base: str, title: str, body: str) -> Change: ...

    async def change(self, number: int) -> Change: ...

    async def update_from_base(self, number: int, expected_head: str) -> Update: ...

    async def merge(self, number: int, expected_head: str, method: MergeMethod, message: str) -> str:
        """The landed revision."""
        ...


class IForgeChecks(Protocol):
    """The checks a forge runs on a revision."""

    async def checks(self, ref: str) -> tuple[Check, ...]:
        """Each check on ``ref``, a revision or a branch whose tip the forge reads."""
        ...

    async def change_checks(self, number: int) -> tuple[Check, ...]:
        """The checks that decide whether change ``number`` may merge, read where the forge ran them."""
        ...

    async def rerun_check(self, id: str) -> bool:
        """Whether the forge accepted the re-run."""
        ...


@dataclass(frozen=True)
class RepositoryForge:
    """One repository's forge roles, its credential bound inside, and what its forge offers."""

    repository_id: RepositoryId
    refs: IForgeRefs
    changes: IForgeChanges
    checks: IForgeChecks
    offered: frozenset[Capability]


#: Each resolved repository's forge.
type VisitForges = Mapping[RepositoryId, RepositoryForge]
