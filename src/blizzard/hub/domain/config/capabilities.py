"""The closed vocabulary of what a forge adapter can do, so the domain reasons about a repository's forge without
importing the seam."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from blizzard.hub.domain.config.forges import ConfiguredForge
    from blizzard.hub.domain.config.repositories import RepositoryId


class Capability(StrEnum):
    """One thing a forge adapter can do — a stable string the seam's operations and the actions' requirements share."""

    REFS_READ = "refs.read"
    COMPARE = "compare"
    COMMITS_READ = "commits.read"
    CHANGES_OPEN = "changes.open"
    CHANGES_READ = "changes.read"
    CHANGES_UPDATE_FROM_BASE = "changes.update-from-base"
    CHANGES_MERGE_MERGE = "changes.merge.merge"
    CHANGES_MERGE_SQUASH = "changes.merge.squash"
    CHANGES_MERGE_REBASE = "changes.merge.rebase"
    CHECKS_READ = "checks.read"
    CHECKS_RERUN = "checks.rerun"
    REFS_ADVANCE = "refs.advance"
    BRANCHES_MERGE = "branches.merge"
    REFS_CREATE = "refs.create"
    REFS_DELETE = "refs.delete"


class IForgeOffers(Protocol):
    """What a forge offers — for ``repository`` when one is named, the forge's kind for one not yet created."""

    def offered(self, forge: ConfiguredForge, repository: RepositoryId | None = None) -> frozenset[Capability]: ...
