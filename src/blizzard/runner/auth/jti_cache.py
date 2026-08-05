"""The single-use ``jti`` replay-cache seam (issue #95, decision D4).

Store-backed (``jwt_jti_seen``) rather than in-memory, so the single-use guarantee
survives a runner restart. **Crash correctness (D4):** ``check_and_record`` is a
single-transaction insert under the ``jti`` primary key, so no crash lands in a partial
write — no ``bzh:crash-point-registry`` entry or ``bzh:invariant-checker`` assertion."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class IJtiCache(Protocol):
    def check_and_record(self, jti: str, *, aud: str, expires_at: datetime) -> bool:
        """Atomically check-not-seen-and-record ``jti``. Returns ``True`` when this is
        the first time ``jti`` has been presented (a fresh, single-use admission);
        ``False`` when it was already recorded (a replay — the caller rejects the
        token outright)."""
        ...
