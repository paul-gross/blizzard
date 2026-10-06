"""The single-use ``jti`` replay-cache seam.

Durable, so the single-use guarantee survives a runner restart. ``check_and_record`` is
atomic: no crash leaves a partial record, so it carries no ``bzh:crash-point-registry`` entry
or ``bzh:invariant-checker`` assertion."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class IJtiCache(Protocol):
    def check_and_record(self, jti: str, *, aud: str, expires_at: datetime) -> bool:
        """Atomically check-not-seen-and-record ``jti``. Returns ``True`` when this is
        the first time ``jti`` has been presented (a fresh, single-use admission);
        ``False`` when it was already recorded (a replay)."""
        ...
