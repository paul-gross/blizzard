"""The runner's own local session — a signed, stateless ``HttpOnly`` cookie (issue #95).

A small JSON payload HMAC-signed with a per-process secret minted at daemon startup
(``bzh:injected-clock`` for the timestamps), so it costs no store schema and a restart
invalidates every live session (pinned by ``tests/test_pin_runner_misc.py``)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from blizzard.auth_core import Role
from blizzard.foundation.store.utc import iso_utc

SESSION_COOKIE_NAME = "bz_runner_session"
#: Runner sessions are short (issue #95): hours, not days — renewal is a silent bounce
#: through the hub, so a short TTL costs nothing but an invisible round trip.
SESSION_TTL = timedelta(hours=8)


@dataclass(frozen=True)
class RunnerSession:
    username: str
    role: Role
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class SessionCookie:
    """The cookie's two halves — a base64url JSON payload and its HMAC-SHA256 tag — under
    the one per-process secret both minting and reading are keyed on."""

    secret: bytes

    def mint(self, session: RunnerSession) -> str:
        payload = json.dumps(
            {
                "username": session.username,
                "role": session.role.value,
                "issued_at": iso_utc(session.issued_at),
                "expires_at": iso_utc(session.expires_at),
            }
        ).encode()
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
        return f"{encoded.decode()}.{self._sign(encoded)}"

    def read(self, cookie: str, *, now: datetime) -> RunnerSession | None:
        """The signed cookie's contents, or ``None`` on a bad signature, malformed payload,
        or an expired session — the caller (``runner/auth/federation.py``'s
        ``require_human_session``) treats every one of these as "no session"."""
        try:
            encoded, signature = cookie.split(".", 1)
        except ValueError:
            return None
        if not hmac.compare_digest(signature, self._sign(encoded.encode())):
            return None
        try:
            padded = encoded + "=" * (-len(encoded) % 4)
            raw = json.loads(base64.urlsafe_b64decode(padded.encode()))
            expires_at = datetime.fromisoformat(raw["expires_at"])
            issued_at = datetime.fromisoformat(raw["issued_at"])
            role = Role(raw["role"])
            username = str(raw["username"])
        except (ValueError, KeyError, TypeError):
            return None
        if expires_at <= now:
            return None
        return RunnerSession(username=username, role=role, issued_at=issued_at, expires_at=expires_at)

    def _sign(self, value: bytes) -> str:
        return hmac.new(self.secret, value, hashlib.sha256).hexdigest()
