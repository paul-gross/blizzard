"""The single-use ``jti`` replay-cache seam (issue #95, decision D4).

Store-backed (a runner-store table, ``jwt_jti_seen``) rather than in-memory: the
single-use guarantee then survives a runner restart within the JWT's own short
lifetime, at negligible cost. The concrete SQLAlchemy adapter lives at
``internal/jti_cache_repository.py`` (``bzh:dependency-inversion``); this Protocol is
all ``runner/auth/federation.py`` depends on.

**Crash correctness (D4).** :meth:`IJtiCache.check_and_record` is a single-transaction
insert under the ``jti`` primary key, so there is no partial-write window a crash could
land in — and it is a store-level PK constraint, not a derived cross-fact invariant.
No ``bzh:crash-point-registry`` entry and no ``bzh:invariant-checker`` assertion are
required; recorded here explicitly rather than left implicit.
"""

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
