"""``jwt_jti_seen`` adapter over ``RunnerStoreConnections`` (package-private, issue #95).

The caller sees only :class:`~blizzard.runner.auth.jti_cache.IJtiCache`. ``IntegrityError``
is the one ``sqlalchemy`` name held locally — that collision *is* the replay check (D6).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.exc import IntegrityError

from blizzard.foundation.clock import IClock
from blizzard.runner.auth.jti_cache import IJtiCache
from blizzard.runner.store.internal.base import RunnerStoreConnections
from blizzard.runner.store.schema import jwt_jti_seen


class JtiCacheRepository:
    """Read-write ``jwt_jti_seen`` adapter over ``RunnerStoreConnections``."""

    def __init__(self, store: RunnerStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def check_and_record(self, jti: str, *, aud: str, expires_at: datetime) -> bool:
        # A single-txn insert under the `jti` primary key IS the check-and-record (D4);
        # the prune ahead of it drops only expired rows (tests/test_runner_jti_cache.py).
        try:
            with self._store.begin() as conn:
                conn.execute(jwt_jti_seen.delete().where(jwt_jti_seen.c.expires_at < self._clock.now()))
                conn.execute(jwt_jti_seen.insert().values(jti=jti, aud=aud, expires_at=expires_at))
        except IntegrityError:
            return False
        return True


def _conforms(x: JtiCacheRepository) -> IJtiCache:
    return x
