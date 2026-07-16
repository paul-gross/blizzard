"""Shared component-test scaffolding — a fully-wired hub over a tmp sqlite store.

Builds the store-backed ``host`` composition with the two external seams — the forge
delivery and the PM read — replaced by in-process fakes (``bzh:pluggable-seams``): a
:class:`FakeForge` that records lands and lets a test arm a conflict, and a
:class:`FakePmSource` that returns canned issue text. The clock is a
:class:`~blizzard.foundation.clock.FixedClock` the test can advance, so ids order and
timestamps are deterministic (``bzh:injected-clock``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.app import create_app
from blizzard.hub.composition import HubServices, build_services
from blizzard.hub.config import HubConfig
from blizzard.hub.delivery.forge import (
    IForgeDelivery,
    LandingDisposition,
    LandingRequest,
    LandingResult,
    PrDisposition,
    PrHandle,
    PrState,
)
from blizzard.hub.domain.work import PmPointer
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.pm.source import IPmSource, PmItem, PmSourceError
from blizzard.hub.runtime import migration_runner


class FakeForge:
    """An in-process :class:`IForgeDelivery` — records lands/opens, arms conflicts by repo.

    For the open-pr mode (D-059): ``open_pr`` mints an incrementing PR number and records
    the request; a test drives a PR's fate with :meth:`mark_merged`/:meth:`mark_closed`,
    and ``check_pr`` reports the disposition the way a poll would (D-065). A repo already
    opened (same branch) reuses its handle, mirroring the real adapter's crash-safe reuse.
    """

    def __init__(self) -> None:
        self.landed: list[LandingRequest] = []
        self.conflict_repos: set[str] = set()
        self.opened: list[LandingRequest] = []
        self._next_pr = 1
        self._handles: dict[tuple[str, str], PrHandle] = {}
        self._state: dict[tuple[str, int], PrState] = {}

    def land(self, request: LandingRequest) -> LandingResult:
        if request.repo in self.conflict_repos:
            return LandingResult(disposition=LandingDisposition.CONFLICT, landed_commit=None, detail="armed conflict")
        self.landed.append(request)
        return LandingResult(disposition=LandingDisposition.LANDED, landed_commit=f"merged-{request.commit_hash}")

    def open_pr(self, request: LandingRequest) -> PrHandle:
        key = (request.repo, request.branch_name)
        if key in self._handles:
            return self._handles[key]  # reuse — the redelivery/crash-window path
        number = self._next_pr
        self._next_pr += 1
        handle = PrHandle(repo=request.repo, number=number, url=f"http://forge/{request.repo}/pull/{number}")
        self._handles[key] = handle
        self._state[(request.repo, number)] = PrState(disposition=PrDisposition.OPEN)
        self.opened.append(request)
        return handle

    def check_pr(self, handle: PrHandle) -> PrState:
        return self._state.get((handle.repo, handle.number), PrState(disposition=PrDisposition.OPEN))

    def mark_merged(self, repo: str, number: int, *, landed_commit: str = "merged-sha") -> None:
        self._state[(repo, number)] = PrState(disposition=PrDisposition.MERGED, landed_commit=landed_commit)

    def mark_closed(self, repo: str, number: int) -> None:
        self._state[(repo, number)] = PrState(disposition=PrDisposition.CLOSED)


def _conforms_fake_forge(x: FakeForge) -> IForgeDelivery:
    return x


class FakePmSource:
    """An in-process :class:`IPmSource` — canned body + comments per pointer URL.

    A default ``body``/``comments`` answers every pointer; ``by_url`` overrides the item for
    specific pointer URLs (a grouped chunk reads distinct items), and ``fail_urls`` raises
    :class:`PmSourceError` for a URL to exercise the per-pointer forge-failure degradation."""

    def __init__(
        self,
        *,
        body: str = "issue body",
        comments: list[str] | None = None,
        by_url: dict[str, PmItem] | None = None,
        fail_urls: set[str] | None = None,
    ) -> None:
        self.body = body
        self.comments = comments or []
        self.by_url = by_url or {}
        self.fail_urls = fail_urls or set()
        self.fetched: list[str] = []

    def fetch(self, pointer: PmPointer) -> PmItem:
        self.fetched.append(pointer.url)
        if pointer.url in self.fail_urls:
            raise PmSourceError(f"forge unreachable for {pointer.url}")
        if pointer.url in self.by_url:
            return self.by_url[pointer.url]
        return PmItem(body=self.body, comments=list(self.comments))


def _conforms_fake_pm(x: FakePmSource) -> IPmSource:
    return x


def github_double(*, conflict_branches: set[str] | None = None, issues: dict[str, dict] | None = None) -> TestClient:
    """A tiny GitHub-shaped forge double for the real HTTP adapters (D-047/D-057).

    Rather than couple this repo to ``blizzard-mock`` as a dev dependency (a separate
    uv project), the adapter HTTP shaping is exercised against this minimal
    GitHub-REST-v3 surface — issue read + comments, PR create + merge. Wrapped in a
    ``TestClient`` (itself an ``httpx.Client``) so the sync adapters drive it directly.
    """
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    conflict = conflict_branches or set()
    issue_store = issues or {}
    app = FastAPI()
    state: dict[str, object] = {"next_pull": 1, "pulls": {}}

    @app.get("/repos/{owner}/{repo}/issues/{number}")
    def get_issue(owner: str, repo: str, number: int) -> dict:
        key = f"{owner}/{repo}#{number}"
        data = issue_store.get(key, {"body": f"issue {number}", "comments": []})
        return {"number": number, "title": f"issue {number}", "body": data["body"]}

    @app.get("/repos/{owner}/{repo}/issues/{number}/comments")
    def get_comments(owner: str, repo: str, number: int) -> list[dict]:
        key = f"{owner}/{repo}#{number}"
        data = issue_store.get(key, {"body": "", "comments": []})
        return [{"body": c} for c in data["comments"]]

    @app.post("/repos/{owner}/{repo}/pulls")
    def create_pull(owner: str, repo: str, body: dict) -> JSONResponse:
        pulls = state["pulls"]  # type: ignore[index]
        if any(p["state"] == "open" and p["head"] == body["head"] for p in pulls.values()):  # type: ignore[union-attr]
            # GitHub 422s a second PR for the same head — the redelivery reuse path.
            return JSONResponse(status_code=422, content={"message": "A pull request already exists"})
        number = int(state["next_pull"])  # type: ignore[arg-type]
        state["next_pull"] = number + 1
        state["pulls"][number] = {  # type: ignore[index]
            "head": body["head"],
            "base": body["base"],
            "merged": False,
            "state": "open",
            "merge_commit_sha": None,
        }
        return JSONResponse(
            status_code=201,
            content={
                "number": number,
                "html_url": f"http://forge/{owner}/{repo}/pull/{number}",
                "head": {"ref": body["head"]},
            },
        )

    @app.get("/repos/{owner}/{repo}/pulls")
    def list_pulls(owner: str, repo: str, state_: str = "open") -> list[dict]:
        pulls = state["pulls"]  # type: ignore[index]
        return [
            {
                "number": n,
                "head": {"ref": p["head"]},
                "state": p["state"],
                "html_url": f"http://forge/{owner}/{repo}/pull/{n}",
            }
            for n, p in pulls.items()  # type: ignore[union-attr]
            if p["state"] == state_
        ]

    @app.get("/repos/{owner}/{repo}/pulls/{number}")
    def get_pull(owner: str, repo: str, number: int) -> dict:
        p = state["pulls"].get(number, {})  # type: ignore[union-attr]
        return {
            "number": number,
            "head": {"ref": p.get("head")},
            "merged": p.get("merged", False),
            "state": p.get("state", "open"),
            "merge_commit_sha": p.get("merge_commit_sha"),
        }

    @app.put("/repos/{owner}/{repo}/pulls/{number}/merge")
    def merge_pull(owner: str, repo: str, number: int, body: dict) -> JSONResponse:
        pull = state["pulls"].get(number, {})  # type: ignore[union-attr]
        if pull.get("head") in conflict:
            return JSONResponse(status_code=409, content={"message": "not mergeable"})
        merge_sha = f"merged-{body.get('sha')}"
        pull.update({"merged": True, "state": "closed", "merge_commit_sha": merge_sha})
        return JSONResponse(status_code=200, content={"sha": merge_sha, "merged": True, "message": "ok"})

    client = TestClient(app)
    client.forge_state = state  # type: ignore[attr-defined]  # tests flip PR fate (e.g. close-without-merge)
    return client


@dataclass
class HubHarness:
    """A wired hub app plus the collaborators a test drives and asserts against."""

    client: TestClient
    services: HubServices
    forge: FakeForge
    pm: FakePmSource
    clock: FixedClock
    events: EventBroker = field(default_factory=EventBroker)


def build_hub(
    tmp_path: Path,
    *,
    forge: FakeForge | None = None,
    pm: FakePmSource | None = None,
    base_branch: str = "main",
) -> HubHarness:
    """A migrated, fully-wired hub over ``tmp_path`` with fake external seams."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    config = HubConfig(root=tmp_path, db_url=db_url)
    migration_runner(config).upgrade("head")

    forge = forge or FakeForge()
    pm = pm or FakePmSource()
    clock = FixedClock(datetime(2026, 7, 13, tzinfo=UTC))
    events = EventBroker()
    engine = create_engine_from_url(db_url)
    services = build_services(engine, forge=forge, events=events, pm_source=pm, clock=clock, base_branch=base_branch)
    app = create_app(config, services=services)
    return HubHarness(client=TestClient(app), services=services, forge=forge, pm=pm, clock=clock, events=events)


def parse_sse_frames(text: str) -> list[dict[str, str]]:
    """Parse an ``text/event-stream`` payload into ``[{id, event, data}]`` dicts.

    Reserved comment lines (``:``-prefixed) and keepalives are skipped; a blank line
    terminates one frame. Shared by the broker-buffer read and the direct stream-generator
    drain so both assert the exact wire bytes an ``EventSource`` would parse.
    """
    events: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith(":"):
            continue  # a comment (reserved / keepalive)
        if line.startswith("id:"):
            current["id"] = line[3:].strip()
        elif line.startswith("event:"):
            current["event"] = line[6:].strip()
        elif line.startswith("data:"):
            current["data"] = line[5:].strip()
        elif line == "" and "event" in current:
            events.append(current)
            current = {}
    if "event" in current:
        events.append(current)
    return events


async def drain_stream(broker: EventBroker, *, last_event_id: int = 0) -> list[dict[str, str]]:
    """Read the SSE endpoint's own generator to the end of its replay tail (a real stream read).

    Starlette's ``TestClient`` (httpx ``ASGITransport``) buffers a whole response body, so it
    cannot consume the hub's *infinite* live stream incrementally. Instead this drives the
    route's async generator directly with a request that reports itself disconnected, so the
    generator emits the reserved comment plus the buffered replay tail (newer than
    ``last_event_id``) and then returns at the first liveness check — exactly the bytes a
    reconnecting ``EventSource`` receives before live events begin.
    """
    from blizzard.hub.api.events import _stream

    class _DisconnectedRequest:
        async def is_disconnected(self) -> bool:
            return True

    chunks: list[bytes] = []
    async for chunk in _stream(broker, _DisconnectedRequest(), last_event_id=last_event_id):  # type: ignore[arg-type]
        chunks.append(chunk)
    return parse_sse_frames(b"".join(chunks).decode())


def emitted_events(hub: HubHarness, *, since: int = 0) -> list[dict[str, str]]:
    """The typed events the hub published after ``since`` — the broker's replay tail.

    This is exactly what a subscriber connecting with ``Last-Event-ID: since`` replays off
    the stream (``EventBroker.replay_since``), so asserting on it asserts SSE emission
    without the buffering-transport limitation. Each dict carries ``id``, ``event``, ``data``.
    """
    return [{"id": str(e.id), "event": e.type, "data": e.data} for e in hub.events.replay_since(since)]


def ingest(hub: HubHarness, pointers: list[dict], *, promote: bool = True) -> str:
    """Ingest ``pointers`` into one chunk and (by default) promote it to ready (D-103).

    Ingest now mints a chunk in the not-ready resting state, so most tests — which expect
    the chunk claimable/in the ready queue — promote it in the same breath. Pass
    ``promote=False`` to assert the not-ready default itself.
    """
    resp = hub.client.post("/api/chunks", json={"pointers": pointers})
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]
    if promote:
        promoted = hub.client.post(f"/api/chunks/{chunk_id}/promote")
        assert promoted.status_code == 202, promoted.text
    return chunk_id


def assert_utc_iso(value: object) -> None:
    """Assert ``value`` is a literal ISO-8601 string carrying an explicit UTC offset.

    Pins the wire **bytes**, not a parsed-then-compared value (issue #28,
    ``bzh:utc-instants``): a naive string re-parses fine with ``datetime.fromisoformat``
    on the same box that emitted it, so only the literal trailing designator
    (``+00:00`` / ``Z``) catches the naive-serialization bug — the finale's literal-bytes
    insight, generalized.
    """
    assert isinstance(value, str), f"expected an ISO-8601 timestamp string, got {value!r}"
    assert value.endswith("+00:00") or value.endswith("Z"), f"timestamp missing a UTC offset: {value!r}"
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def assert_all_timestamps_utc(payload: object) -> None:
    """Recursively walk a response body, applying :func:`assert_utc_iso` to every ``*_at`` key.

    A route test calls this once on its response; a route that later adds a seventh
    timestamp field is covered without the test itself changing.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.endswith("_at") and value is not None:
                assert_utc_iso(value)
            else:
                assert_all_timestamps_utc(value)
    elif isinstance(payload, list):
        for item in payload:
            assert_all_timestamps_utc(item)


def report_lease(hub: HubHarness, chunk_id: str, *, epoch: int, seq: int, runner_id: str = "r1") -> dict:
    """Report a runner-minted ``lease.minted`` fact through POST /events (D-044/D-069).

    Mirrors the real runner flow: after claiming a route, the runner mints its lease
    locally and reports its epoch up through the store-and-forward buffer, which is the
    fence input the completion check consumes. Component tests that submit a completion
    call this first so the hub knows the chunk's latest epoch.
    """
    resp = hub.client.post(
        "/api/events",
        json={
            "runner_id": runner_id,
            "facts": [{"seq": seq, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": epoch}}],
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()
