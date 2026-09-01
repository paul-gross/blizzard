"""The lease resume-intent repository seam — the restart resume-intent mark and its
clear (issue #12/#13)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class IReadLeaseResumeIntentRepository(Protocol):
    """Read-only restart resume-intent queries (held by read-path edges)."""

    def resume_intent_lease_ids(self) -> set[str]:
        """Leases carrying an **open** restart resume-intent.

        A ``resume_intents`` mark with no ``resume_clears`` for the same lease at or
        after it (#12, #13). Empty on any normal tick; non-empty only on the first tick
        after a restart."""
        ...


class IWriteLeaseResumeIntentRepository(IReadLeaseResumeIntentRepository, Protocol):
    """Read-write resume-intent store — held only by the domain (the loop steps)."""

    def record_resume_intent(self, *, lease_id: str, marked_at: datetime) -> None:
        """Mark a lease for same-lease restart-resume at graceful shutdown."""
        ...

    def record_resume_clear(self, *, lease_id: str, cleared_at: datetime) -> None:
        """Clear a lease's resume-intent — the RESUME step resumed or abandoned it."""
        ...
