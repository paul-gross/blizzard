"""A member's open ``GET /api/events/stream`` ends when their membership in the stream's tenant ends, or when the
session it was opened under logs out.

The revocation signals the tenant's broker once its write has committed; the broker ends that user's connections
alone, each after the frames it already holds, and the stream reads no store to learn of it. A logout ends the
connections of its one session on every tenant's broker, leaving the same user's other sessions streaming."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from blizzard.auth_core import Role
from blizzard.foundation.events.broker import EventBroker
from blizzard.foundation.events.stream import Cursor, Stream
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.api.events import _RESERVED_COMMENT
from blizzard.hub.auth.hashing import SessionId
from blizzard.hub.auth.models import User
from tests.support import HubHarness, build_hub, default_tenant, parse_sse_frames, seed_session, seed_user

pytestmark = pytest.mark.component


def _until(condition: Callable[[], bool], *, within: float = 5.0) -> None:
    deadline = time.monotonic() + within
    while not condition():
        assert time.monotonic() < deadline, "the condition never held"
        time.sleep(0.01)


async def test_ending_a_subscriber_ends_its_connections_alone_after_what_they_hold() -> None:
    broker = EventBroker()
    revoked, also_revoked, kept, anonymous = (
        broker.subscribe("usr_a"),
        broker.subscribe("usr_a"),
        broker.subscribe("usr_b"),
        broker.subscribe(),
    )
    broker.publish("queue-changed", {})

    broker.end_subscriber("usr_a")
    broker.publish("queue-changed", {})
    await asyncio.sleep(0)

    assert revoked.ended.is_set() and also_revoked.ended.is_set()
    assert revoked.queue.qsize() == 1, "only the frame handed over before the end reaches the ended connection"
    assert not kept.ended.is_set() and not anonymous.ended.is_set()
    assert kept.queue.qsize() == 2
    assert broker.subscriber_count() == 2


async def test_ending_a_session_ends_its_connections_alone() -> None:
    broker = EventBroker()
    logged_out, other_session, sessionless = (
        broker.subscribe("usr_a", "ses_1"),
        broker.subscribe("usr_a", "ses_2"),
        broker.subscribe("usr_a"),
    )

    broker.end_session("ses_1")
    await asyncio.sleep(0)

    assert logged_out.ended.is_set()
    assert not other_session.ended.is_set() and not sessionless.ended.is_set()
    assert broker.subscriber_count() == 2


def _as(hub: HubHarness, user: User) -> dict[str, str]:
    return {"Cookie": f"bz_session={seed_session(hub, user)}", TENANT_HEADER: default_tenant(hub.engine)}


class _ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


async def test_a_revoked_members_open_stream_ends_and_another_members_keeps_running(tmp_path: Path) -> None:
    """The revoked member's stream is opened through the route; the other member's is the stream the route builds
    for them, held open in this loop, since a test client serves one live stream at a time."""
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="ada", role=Role.ADMIN)
    revoked = seed_user(hub, username="grace", role=Role.GUEST)
    kept = seed_user(hub, username="linus", role=Role.GUEST)
    broker = hub.open_services(default_tenant(hub.engine)).events
    kept_stream = Stream(broker, _ConnectedRequest(), Cursor(0), _RESERVED_COMMENT, owner=kept.user_id).frames()  # type: ignore[arg-type]
    assert await anext(kept_stream) == _RESERVED_COMMENT.encode()
    loop = asyncio.get_running_loop()

    with ThreadPoolExecutor(max_workers=1) as pool:
        revoked_stream = loop.run_in_executor(
            pool, lambda: hub.client.get("/api/events/stream", headers=_as(hub, revoked))
        )
        try:
            deadline = time.monotonic() + 5.0
            while broker.subscriber_count() < 2:
                assert time.monotonic() < deadline, "the revoked member's stream never opened"
                await asyncio.sleep(0.01)
            broker.publish_queue_changed()

            resp = await loop.run_in_executor(
                None, lambda: hub.client.delete(f"/api/members/{revoked.user_id}", headers=_as(hub, admin))
            )
            assert resp.status_code == 204, resp.text

            ended = await asyncio.wait_for(revoked_stream, timeout=2.0)
            broker.publish_runner_changed("rn_after", kind="added")
            kept_frames = [await asyncio.wait_for(anext(kept_stream), timeout=1.0) for _ in range(2)]
            assert broker.subscriber_count() == 1, "the other member's stream is still open"
        finally:
            broker.close()
            await kept_stream.aclose()  # type: ignore[attr-defined]

    assert ended.status_code == 200
    assert [frame["event"] for frame in parse_sse_frames(ended.text)] == ["queue-changed"]
    assert [frame["event"] for frame in parse_sse_frames(b"".join(kept_frames).decode())] == [
        "queue-changed",
        "runner-changed",
    ]


def test_a_role_change_leaves_the_members_stream_open(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="ada", role=Role.ADMIN)
    member = seed_user(hub, username="grace", role=Role.GUEST)
    broker = hub.open_services(default_tenant(hub.engine)).events

    with ThreadPoolExecutor(max_workers=1) as pool:
        stream = pool.submit(hub.client.get, "/api/events/stream", headers=_as(hub, member))
        try:
            _until(lambda: broker.subscriber_count() == 1)
            resp = hub.client.put(
                f"/api/members/{member.user_id}", json={"role": "contributor"}, headers=_as(hub, admin)
            )
            assert resp.status_code == 200, resp.text
            assert broker.subscriber_count() == 1
            broker.publish_queue_changed()
        finally:
            broker.close()
        resp = stream.result(timeout=5.0)

    assert [frame["event"] for frame in parse_sse_frames(resp.text)] == ["queue-changed"]


async def test_a_logged_out_sessions_stream_ends_and_the_users_other_session_keeps_streaming(tmp_path: Path) -> None:
    """The logged-out session's stream is opened through the route; the same user's other session's is the stream
    the route builds for it, held open in this loop, since a test client serves one live stream at a time."""
    hub = build_hub(tmp_path, auth_mode="oauth")
    user = seed_user(hub, username="grace", role=Role.GUEST)
    leaving, staying = seed_session(hub, user), seed_session(hub, user)
    headers = {"Cookie": f"bz_session={leaving}", TENANT_HEADER: default_tenant(hub.engine)}
    broker = hub.open_services(default_tenant(hub.engine)).events
    kept_stream = Stream(
        broker,
        _ConnectedRequest(),  # type: ignore[arg-type]
        Cursor(0),
        _RESERVED_COMMENT,
        owner=user.user_id,
        session=SessionId(staying).hash,
    ).frames()
    assert await anext(kept_stream) == _RESERVED_COMMENT.encode()
    loop = asyncio.get_running_loop()

    with ThreadPoolExecutor(max_workers=1) as pool:
        leaving_stream = loop.run_in_executor(pool, lambda: hub.client.get("/api/events/stream", headers=headers))
        try:
            deadline = time.monotonic() + 5.0
            while broker.subscriber_count() < 2:
                assert time.monotonic() < deadline, "the logged-out session's stream never opened"
                await asyncio.sleep(0.01)
            broker.publish_queue_changed()

            resp = await loop.run_in_executor(None, lambda: hub.client.post("/api/auth/logout", headers=headers))
            assert resp.status_code == 204, resp.text

            ended = await asyncio.wait_for(leaving_stream, timeout=2.0)
            broker.publish_runner_changed("rn_after", kind="added")
            kept_frames = [await asyncio.wait_for(anext(kept_stream), timeout=1.0) for _ in range(2)]
            assert broker.subscriber_count() == 1, "the other session's stream is still open"
        finally:
            broker.close()
            await kept_stream.aclose()  # type: ignore[attr-defined]

    assert ended.status_code == 200
    assert [frame["event"] for frame in parse_sse_frames(ended.text)] == ["queue-changed"]
    assert [frame["event"] for frame in parse_sse_frames(b"".join(kept_frames).decode())] == [
        "queue-changed",
        "runner-changed",
    ]


def test_reconnecting_with_a_logged_out_session_is_refused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    user = seed_user(hub, username="grace", role=Role.GUEST)
    headers = _as(hub, user)
    assert hub.client.post("/api/auth/logout", headers=headers).status_code == 204

    resp = hub.client.get("/api/events/stream", headers=headers)

    assert resp.status_code == 401
