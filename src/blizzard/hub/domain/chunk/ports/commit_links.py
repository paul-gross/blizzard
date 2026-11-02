"""The landed-commit link seam — a forge web address for each commit a chunk landed."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from blizzard.hub.domain.chunk.model import Chunk

#: A landed ``owner/repo`` and sha to its forge commit address, or ``None`` when none is known.
CommitLink = Callable[[str, str], str | None]


def no_commit_link(_repo: str, _sha: str) -> str | None:
    """The lookup a chunk with no forge address to give gets."""
    return None


class IReadLandedCommitLinks(Protocol):
    """Each given chunk's landed-commit lookup, from its repository records' forges."""

    def for_chunks(self, chunks: Sequence[Chunk]) -> dict[str, CommitLink]:
        """Each of ``chunks``' lookups keyed by chunk id, every chunk present; one read of links, one of repository
        records and one of forges serve the whole batch."""
        ...
