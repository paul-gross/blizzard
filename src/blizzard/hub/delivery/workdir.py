"""The per-chunk hub workdir seam (#65).

A disposable, per-chunk filesystem cache under the hub runtime dir: lazily created,
reused across commands and re-runs, expired at any chunk-ending event, with an orphan
janitor. Filesystem I/O stays behind this Protocol (``bzh:dependency-inversion``).
Losing the folder loses time, never correctness (``bzh:facts-not-status``)."""

from __future__ import annotations

from typing import Protocol


class IHubWorkdir(Protocol):
    """Per-chunk hub workdir lifecycle."""

    def ensure(self, chunk_id: str) -> str:
        """The chunk's workdir path, creating it (and any git remotes it needs) if
        this is the first hub-node visit; reused, warm-fetched, on every later call."""
        ...

    def expire(self, chunk_id: str) -> None:
        """Reclaim ``chunk_id``'s workdir — called at ``done`` and any chunk-ending
        event (stop, terminal escalation). Idempotent; a missing folder is a no-op."""
        ...

    def list_orphans(self) -> list[str]:
        """Every chunk id with a workdir on disk — the janitor's sweep input; the
        caller cross-checks each against the store to reclaim only terminal chunks."""
        ...
