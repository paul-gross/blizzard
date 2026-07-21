"""SQLAlchemy adapter for the jti replay-cache seam (package-private, issue #95).

Confines all ``sqlalchemy`` usage here (``bzh:dependency-inversion``); the caller
(``runner/auth/validate.py``) sees only :class:`~blizzard.runner.auth.jti_cache.IJtiCache`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine, delete, insert
from sqlalchemy.exc import IntegrityError

from blizzard.runner.auth.jti_cache import IJtiCache
from blizzard.runner.store.schema import jwt_jti_seen


class JtiCacheRepository:
    """Read-write ``jwt_jti_seen`` adapter over the runner store engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def check_and_record(self, jti: str, *, aud: str, expires_at: datetime) -> bool:
        # A single-txn insert under the `jti` primary key IS the check-and-record
        # (decision D4): a duplicate `jti` fails the insert with `IntegrityError`
        # rather than ever being visible as "not yet seen" to a second caller. The
        # opportunistic prune ahead of it is a separate statement in the same
        # transaction — it only ever removes rows already past their own `expires_at`,
        # so it cannot touch the row this call is about to insert, and it does not
        # weaken the PK insert as the sole check-and-record gate.
        try:
            with self._engine.begin() as conn:
                conn.execute(delete(jwt_jti_seen).where(jwt_jti_seen.c.expires_at < datetime.now(UTC)))
                conn.execute(insert(jwt_jti_seen).values(jti=jti, aud=aud, expires_at=expires_at))
        except IntegrityError:
            return False
        return True


def _conforms(x: JtiCacheRepository) -> IJtiCache:
    return x
