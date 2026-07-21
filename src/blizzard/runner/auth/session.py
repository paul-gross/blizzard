"""The runner's own local session — a signed, stateless ``HttpOnly`` cookie (issue #95).

Unlike the hub's session (a store-backed row resolved by hash, ``hub/auth/sessions.py``)
the runner's session carries no server-side row: it is a small JSON payload
(``username``, ``role``, ``issued_at``, ``expires_at``) HMAC-signed with a per-process
secret minted at daemon startup (``bzh:injected-clock`` for the timestamps; the secret
itself is not a domain concern, so it is plain ``secrets.token_bytes`` at the
composition root, not clock-derived). A restart therefore invalidates every live
session outright — acceptable because re-authentication is a *silent* bounce back
through the hub (issue #95's own design): the human plane 401s, the browser is bounced
to ``GET /api/auth/login``, and if the hub itself still holds a live session the whole
round trip completes with no user-visible prompt. This trades session durability across
a runner restart for zero new store schema — the runner-store table this phase does add
(``jwt_jti_seen``) exists for the anti-replay guarantee, which *must* survive a restart
within its own short window; a *runner-local* session losing itself the moment the
daemon that minted it restarts is not a comparable correctness requirement, and (issue
#95's own text) runner sessions are already meant to be short-lived (hours) with silent
renewal — a hard restart simply forces the next renewal a little early.
"""

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
#: Runner sessions are short (issue #95's own text): hours, not days — renewal is a
#: silent bounce through the hub, so a short TTL costs nothing but an invisible round
#: trip.
SESSION_TTL = timedelta(hours=8)


@dataclass(frozen=True)
class RunnerSession:
    username: str
    role: Role
    issued_at: datetime
    expires_at: datetime


def mint_session_cookie(session: RunnerSession, *, secret: bytes) -> str:
    payload = json.dumps(
        {
            "username": session.username,
            "role": session.role.value,
            "issued_at": iso_utc(session.issued_at),
            "expires_at": iso_utc(session.expires_at),
        }
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = _sign(encoded, secret)
    return f"{encoded.decode()}.{signature}"


def verify_session_cookie(cookie: str, *, secret: bytes, now: datetime) -> RunnerSession | None:
    """The signed cookie's contents, or ``None`` on a bad signature, malformed payload,
    or an expired session — the caller (``runner/auth/federation.py``'s
    ``require_human_session``) treats every one of these as "no session"."""
    try:
        encoded, signature = cookie.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(encoded.encode(), secret)):
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


def _sign(value: bytes, secret: bytes) -> str:
    return hmac.new(secret, value, hashlib.sha256).hexdigest()
