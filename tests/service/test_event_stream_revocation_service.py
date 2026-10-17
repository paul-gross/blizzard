"""A revoked member's open event stream ends — the real hub against the real stub IdP (service tier).

Two members of one tenant, each signed in through the OIDC dance, hold ``GET /api/events/stream`` open over the
wire. Revoking the first member's membership through ``DELETE /api/members/{user_id}`` ends that member's stream
promptly, well inside the stream's keepalive, with no frame after it; the second member's stream keeps running and
still receives a frame published afterwards. Every wait is bounded. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from blizzard.auth_core import Role
from blizzard.foundation.events.stream import DEFAULT_KEEPALIVE_SECONDS
from blizzard.hub.config import OAuthProviderConfig
from tests.service.support import require_stub_idp, service_gate, stub_idp
from tests.service.test_auth_login_service import _SECRET_ENV, _oauth_hub, invite
from tests.support import free_port

pytestmark = [pytest.mark.service, service_gate]

#: How long a revoked member's stream may take to end — a fraction of the keepalive it would otherwise idle on.
_ENDS_WITHIN_SECONDS = 5.0
#: How long a published frame may take to reach a live stream.
_FRAME_WITHIN_SECONDS = 10.0


class _MemberStream:
    """One member's ``GET /api/events/stream``, read on a background thread with the member's session cookie:
    the event names it delivers, in order, and whether the hub has ended it."""

    def __init__(self, base_url: str, cookies: httpx.Cookies) -> None:
        self._client = httpx.Client(
            base_url=base_url, cookies=cookies, timeout=httpx.Timeout(10.0, read=DEFAULT_KEEPALIVE_SECONDS * 2)
        )
        self.events: queue.Queue[str] = queue.Queue()
        self.opened = threading.Event()
        self.ended = threading.Event()
        self.status: int | None = None
        self._response: httpx.Response | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            with self._client.stream("GET", "/api/events/stream") as resp:
                self._response = resp
                self.status = resp.status_code
                event_type: str | None = None
                for raw in resp.iter_lines():
                    self.opened.set()
                    line = raw.strip()
                    if line.startswith("event:"):
                        event_type = line.split(":", 1)[1].strip()
                    elif line.startswith("data:") and event_type:
                        self.events.put(event_type)
                        event_type = None
        except httpx.HTTPError:
            pass
        finally:
            self.opened.set()
            self.ended.set()

    def start(self) -> None:
        self._thread.start()
        assert self.opened.wait(20), "the member's event stream never delivered its first line"
        assert self.status == 200, self.status

    def take(self, *, window: float) -> list[str]:
        """Every event name delivered within ``window`` seconds."""
        seen: list[str] = []
        deadline = time.monotonic() + window
        while time.monotonic() < deadline:
            with contextlib.suppress(queue.Empty):
                seen.append(self.events.get(timeout=0.1))
        return seen

    def await_event(self, name: str, *, within: float) -> list[str]:
        """The event names delivered until ``name`` arrives — failing if it does not within ``within`` seconds."""
        seen: list[str] = []
        deadline = time.monotonic() + within
        while name not in seen:
            remaining = deadline - time.monotonic()
            assert remaining > 0, f"{name} never reached the stream; it delivered {seen}"
            with contextlib.suppress(queue.Empty):
                seen.append(self.events.get(timeout=min(remaining, 0.25)))
        return seen

    def close(self) -> None:
        if self._response is not None:
            with contextlib.suppress(Exception):
                self._response.close()
        self._client.close()
        self._thread.join(timeout=5)


@contextlib.contextmanager
def _member_stream(base_url: str, member: httpx.Client) -> Iterator[_MemberStream]:
    stream = _MemberStream(base_url, member.cookies)
    try:
        stream.start()
        stream.take(window=1.0)  # the connect-time replay, so what follows is live fan-out alone
        yield stream
    finally:
        stream.close()


def _sign_in(idp: httpx.Client, base_url: str, hub_dir: Path, *, subject: str, handle: str, role: Role) -> httpx.Client:
    """A client holding the session the OIDC dance ends in for an invited ``handle`` with ``role``."""
    email = f"{handle}@example.com"
    profile = idp.put(
        "/_levers/profile", json={"subject": subject, "handle": handle, "email": email, "email_verified": True}
    )
    assert profile.status_code < 300, profile.text
    client = httpx.Client(base_url=base_url, timeout=30.0)
    client.get(f"/api/auth/oidc-co/authorize?invite={invite(hub_dir, email, role=role)}", follow_redirects=True)
    assert "bz_session" in client.cookies, f"{handle} did not sign in"
    return client


def test_revoking_a_members_membership_ends_their_open_stream_and_no_other(tmp_path: Path) -> None:
    bin_dir = require_stub_idp()
    idp_port, hub_port = free_port(), free_port()
    base_url = f"http://127.0.0.1:{hub_port}"
    hub_dir = tmp_path / "hub"

    with stub_idp(bin_dir, idp_port) as idp:
        provider = OAuthProviderConfig(
            name="oidc-co",
            type="oidc",
            display_name="Stub SSO",
            client_id="cid",
            client_secret_env=_SECRET_ENV,
            issuer=f"http://127.0.0.1:{idp_port}",
            emails_authoritative=True,
        )
        with (
            _oauth_hub(hub_dir, hub_port, providers=(provider,)),
            contextlib.ExitStack() as clients,
        ):
            admin = _sign_in(idp, base_url, hub_dir, subject="1", handle="ada", role=Role.ADMIN)
            clients.callback(admin.close)
            revoked = _sign_in(idp, base_url, hub_dir, subject="2", handle="grace", role=Role.GUEST)
            clients.callback(revoked.close)
            kept = _sign_in(idp, base_url, hub_dir, subject="3", handle="linus", role=Role.GUEST)
            clients.callback(kept.close)
            members = admin.get("/api/members")
            assert members.status_code == 200, members.text
            revoked_id = next(m["user_id"] for m in members.json() if m["username"] == "grace")

            with _member_stream(base_url, revoked) as revoked_stream, _member_stream(base_url, kept) as kept_stream:
                assert admin.post("/api/runners", json={"name": "runner-before"}).status_code == 201
                revoked_stream.await_event("runner-changed", within=_FRAME_WITHIN_SECONDS)
                kept_stream.await_event("runner-changed", within=_FRAME_WITHIN_SECONDS)
                revoked_stream.take(window=0.5)  # anything else the add published, before the revocation

                revoking = admin.delete(f"/api/members/{revoked_id}")
                assert revoking.status_code == 204, revoking.text

                assert revoked_stream.ended.wait(_ENDS_WITHIN_SECONDS), "the revoked member's stream is still open"
                held = revoked_stream.take(window=0.2)  # what the stream already held when it ended
                assert admin.post("/api/runners", json={"name": "runner-after"}).status_code == 201
                kept_stream.await_event("runner-changed", within=_FRAME_WITHIN_SECONDS)
                assert not kept_stream.ended.is_set(), "the other member's stream ended with the revocation"
                assert "runner-changed" not in held
                assert revoked_stream.take(window=0.5) == [], "the revoked member's stream sent a frame after it ended"
