"""The chunk-delivery repository seam — landed repos, the pending
close-intent and materialization tail a delivered chunk leaves behind."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.proposals import StampedWorkItemProposal
from blizzard.hub.domain.work import PendingCloseIntent, WorkItemCloseOutcome, WorkItemMaterializationOutcome, WorkRef


class IReadChunkDeliveryRepository(Protocol):
    """Read-only chunk-delivery access."""

    def landed_repos(self, chunk_id: str) -> set[str]:
        """The repos already landed for a chunk — the delivery reconciliation skip-set."""
        ...

    def count_landed_since(self, repo: str, since: datetime) -> int:
        """How many `delivery_repo_landed` rows `repo` has recorded strictly after
        `since`; "landed" is this table's own fact, not a commit count."""
        ...

    def pending_close_intents(self) -> list[PendingCloseIntent]:
        """Every ``(chunk_id, ref)`` pair carrying a pending ``close_intents`` row,
        due or not, with its own ``close_intent_attempts`` history; a chunk in the ephemeral
        set is excluded even if its intent enqueued before it was grouped or deleted.
        Whether an intent is due is the drainer's call (``close_intent_is_due``), never
        this read's."""
        ...

    def unmaterialized_proposals(self) -> list[StampedWorkItemProposal]:
        """Every not-yet-judged proposal of a chunk that has delivered — a
        ``transitions`` row at ``to_node_id == RESERVED_TERMINAL``, regardless of whether
        a runner-node's own transition or a hub-node's ``release_route`` transition wrote
        it, excluding the ephemeral (grouped/deleted), any proposal already carrying a
        ``work_item_materializations`` row, and any struck proposal. Reads status nowhere:
        a hand-completed or later-stopped chunk is included or excluded purely by whether
        it actually delivered."""
        ...


class IWriteChunkDeliveryRepository(IReadChunkDeliveryRepository, Protocol):
    """Read-write chunk-delivery access."""

    def record_delivery_repo_landed(self, chunk_id: str, *, repo: str, commit_hash: str, at: datetime) -> None: ...
    def record_delivery_landed(self, chunk_id: str, *, at: datetime) -> None: ...

    def finalize_delivery(
        self,
        chunk_id: str,
        *,
        from_node_id: str,
        to_node_id: str,
        choice_name: str,
        epoch: int,
        runner_id: str,
        transition_id: str,
        at: datetime,
    ) -> bool:
        """Land the terminal delivery atomically and idempotently — one transaction, a
        no-op if already landed (crash recovery). Returns True iff it wrote."""
        ...

    def record_work_item_closure(
        self, chunk_id: str, *, pointer: WorkRef, outcome: WorkItemCloseOutcome, reason: str | None, at: datetime
    ) -> bool:
        """Append one closure-attempt outcome fact, idempotent per ``(chunk_id,
        pointer.source, pointer.ref, outcome)``. ``reason`` carries the failure/gone
        detail; ``None`` for ``closed``. A ``closed``/``gone`` outcome also retires the
        matching pending ``close_intents`` row, in the same transaction — never a
        ``failed`` one's. A ``failed`` outcome instead appends a ``close_intent_attempts``
        row for its matching intent, in the same transaction — the
        backoff clock's own tick; a pointer with no matching pending intent (never
        enqueued) records none. Returns True iff it wrote a fresh outcome row."""
        ...

    def record_close_attempt_skipped(self, intent_id: int, *, at: datetime) -> None:
        """Append one ``close_intent_attempts`` row for ``intent_id`` —
        the backoff clock's own tick for an intent no bound closer answered this pass.
        A single insert, its own transaction: unlike a failed close, there is no outcome
        fact or retirement to fold it alongside."""
        ...

    def record_work_item_materialization(
        self,
        proposal_id: str,
        *,
        outcome: WorkItemMaterializationOutcome,
        pointer: WorkRef | None,
        reason: str | None,
        at: datetime,
    ) -> bool:
        """Append one proposal's terminal judgment, idempotent per ``proposal_id`` —
        the standalone recorder for an ``unresolved`` outcome, which mints or mutates no
        work item. ``pointer`` is the targeted item for an unresolvable ``update``, and
        ``None`` for an unresolvable ``create``. Returns True iff it wrote a fresh row."""
        ...
