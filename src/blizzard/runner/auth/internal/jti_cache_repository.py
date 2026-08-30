"""SQLAlchemy adapter for the jti replay-cache seam (package-private, issue #95).

Confines all ``sqlalchemy`` usage here (``bzh:dependency-inversion``); the caller
(``runner/auth/validate.py``) sees only :class:`~blizzard.runner.auth.jti_cache.IJtiCache`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Engine, delete, insert
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.clock import IClock
from blizzard.runner.auth.jti_cache import IJtiCache
from blizzard.runner.store.schema import jwt_jti_seen


class JtiCacheRepository:
    """Read-write ``jwt_jti_seen`` adapter over the runner store engine."""

    def __init__(self, engine: Engine, clock: IClock) -> None:
        self._engine = engine
        self._clock = clock

    def check_and_record(self, jti: str, *, aud: str, expires_at: datetime) -> bool:
        # A single-txn insert under the `jti` primary key IS the check-and-record (D4);
        # the prune ahead of it drops only expired rows (tests/test_runner_jti_cache.py).
        try:
            with self._engine.begin() as conn:
                conn.execute(delete(jwt_jti_seen).where(jwt_jti_seen.c.expires_at < self._clock.now()))
                conn.execute(insert(jwt_jti_seen).values(jti=jti, aud=aud, expires_at=expires_at))
        except IntegrityError:
            return False
        return True


def _conforms(x: JtiCacheRepository) -> IJtiCache:
    return x
