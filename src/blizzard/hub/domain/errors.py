"""General chunk-resolution errors shared across the domain layer.

Homed neutrally rather than on any one service's own module, so a service raising it
never forces an importer of that service to pull in the rest of that service's own
dependencies (issue #460)."""

from __future__ import annotations


class ChunkNotFound(LookupError):
    """A named chunk does not exist (or was grouped or deleted away)."""

    def __init__(self, chunk_id: str) -> None:
        super().__init__(f"unknown chunk {chunk_id}")
        self.chunk_id = chunk_id
