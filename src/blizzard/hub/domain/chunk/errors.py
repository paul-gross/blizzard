"""General chunk-resolution errors shared across the domain layer."""

from __future__ import annotations


class ChunkNotFound(LookupError):
    """A named chunk does not exist (or was grouped or deleted away)."""

    def __init__(self, chunk_id: str) -> None:
        super().__init__(f"unknown chunk {chunk_id}")
        self.chunk_id = chunk_id
