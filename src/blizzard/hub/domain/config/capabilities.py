"""The closed vocabulary of what a forge adapter can do, so the domain reasons about a repository's forge without
importing the seam."""

from __future__ import annotations

from enum import StrEnum


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
