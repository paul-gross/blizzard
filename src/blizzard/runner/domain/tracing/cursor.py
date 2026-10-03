"""The runner's trace export cursor key.

Contract: ``blizzard-product:/delivered/tracing/runner-spans/spec/emission.md`` §The cursor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, order=True)
class LeaseCursorKey:
    """A position in the total order of closed leases: first-closure time, then lease id.

    :meth:`opening` is the position just before every lease that closes at an instant."""

    at: datetime
    lease_id: str = ""

    @classmethod
    def opening(cls, at: datetime) -> LeaseCursorKey:
        return cls(at)
