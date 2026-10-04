"""The runner's own local session — a signed, stateless ``HttpOnly`` cookie.

A small JSON payload HMAC-signed with the runner's session secret (``bzh:injected-clock`` for
the timestamps), so it costs no store schema. The secret is read from the env var the config
names, so a session survives a restart until its TTL; with none configured the daemon mints a
fresh per-process secret and a restart invalidates every live session (pinned by
``tests/test_pin_runner_misc.py``). Each runner needs its own secret."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from blizzard.auth_core import Role
from blizzard.foundation.roles import domain_model
from blizzard.foundation.store.utc import iso_utc

_COOKIE_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9!#$%&'*+.^_`|~-]")
#: Runner sessions are short: hours, not days — renewal is a silent bounce
#: through the hub, so a short TTL costs nothing but an invisible round trip.
SESSION_TTL = timedelta(hours=8)


@domain_model
@dataclass(frozen=True)
class CookieNames:
    """The runner's cookie names, namespaced by ``runner_id``. Browsers scope cookies by host and
    ignore the port, so two same-host runners would otherwise share one jar and overwrite each
    other's session. Characters outside the RFC 6265 cookie-name ``token`` set become ``_``."""

    runner_id: str

    @property
    def _suffix(self) -> str:
        return _COOKIE_NAME_UNSAFE.sub("_", self.runner_id)

    @property
    def session(self) -> str:
        return f"bz_runner_session_{self._suffix}"

    @property
    def bounce_state(self) -> str:
        return f"bz_runner_bounce_state_{self._suffix}"

    @property
    def bounce_return(self) -> str:
        return f"bz_runner_bounce_return_{self._suffix}"


@domain_model
@dataclass(frozen=True)
class RunnerSession:
    username: str
    role: Role
    issued_at: datetime
    expires_at: datetime


@domain_model
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
