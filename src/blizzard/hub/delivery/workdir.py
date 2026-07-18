"""The per-chunk hub workdir seam (#65).

A generic hub command node's commands run somewhere: a disposable, per-chunk
filesystem cache under the hub runtime dir. The lifecycle — lazily created,
reused across commands/re-runs/subsequent hub nodes, expired at ``done`` and any
chunk-ending event, with an orphan janitor — is filesystem I/O and stays behind this
Protocol (``bzh:dependency-inversion``, ``bzh:domain-core``): the executor never
imports ``pathlib``/``shutil`` directly, it asks for a path.

Losing the folder loses time, never correctness — hub facts/marker artifacts are the
only truth (``bzh:facts-not-status``); a command tolerates an empty/missing folder
(the adapter's first-use full clone, or a later warm ``git fetch``). Never a shallow
clone (spike #68 finding 4 — shallow refuses an "unrelated histories" merge).
"""

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
