"""Shared component-test scaffolding — a fully-wired hub over a tmp sqlite store.

Builds the store-backed ``host`` composition with the two external seams — the forge
delivery and the PM read — replaced by in-process fakes (``bzh:pluggable-seams``): a
:class:`FakeForge` that records lands and lets a test arm a conflict, and a
:class:`FakePmSource` that returns canned issue text, wired into the hub through a
:class:`~blizzard.hub.pm.registry.PmSourceRegistry` (D-106) the same way the real
factory would. The clock is a :class:`~blizzard.foundation.clock.FixedClock` the test
can advance, so ids order and timestamps are deterministic (``bzh:injected-clock``).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.app import create_app
from blizzard.hub.composition import HubServices, build_services
from blizzard.hub.config import HubConfig, PmSourceConfig
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
from blizzard.hub.pm.registry import PmSourceRegistry
from blizzard.hub.pm.source import IPmSource, PmItem, PmSourceError, UnknownSource
from blizzard.hub.runtime import migration_runner

# The issue-shaped pointer URL FakePmSource renders a label/web-url from — a small,
# local echo of the GitHub adapter's own grammar (``pm/internal/github_pm_source.py``),
# kept independent so this fake doesn't reach into an adapter's internals. Schemeless-
# tolerant (``(?:^|/)``) to match the adapter's own D-107 fix.
_ISSUE_RE = re.compile(r"(?:^|/)(?:repos/)?(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<number>\d+)")


def _repo_of(url: str) -> str | None:
    """``owner/repo`` from a pointer URL, independent of the issue shape (D-107) — the
    same repo-membership-only extraction ``github_pm_source.py``'s own copy performs, so
    a non-issue-shaped pointer at a matching repo still resolves (label degrades to
    ``None``; ownership does not)."""
    path = urlsplit(url).path.strip("/") or url.strip("/")
    segments = [s for s in path.split("/") if s]
    if segments and segments[0] == "repos":
        segments = segments[1:]
    if len(segments) < 2:
        return None
    return f"{segments[0]}/{segments[1]}"


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

    Still keyed on ``pointer.url`` (the pointer hasn't grown a ``source`` field yet —
    that's Phase 3, D-105). A default ``body``/``comments`` answers every pointer;
    ``by_url`` overrides the item for specific pointer URLs (a grouped chunk reads
    distinct items), and ``fail_urls`` raises :class:`PmSourceError` for a URL to
    exercise the per-pointer forge-failure degradation. ``name`` is this fake's
    registered source name — the prefix its ``label`` renders under, mirroring a real
    binding's configured ``name`` (D-106/D-108). ``repo`` is the ``owner/repo`` this fake
    is pinned to (D-107) — :meth:`owns` compares a pointer's URL against it, mirroring
    the real adapter's repo-matching resolution; two ``FakePmSource``s with distinct
    ``repo``s exercise the two-sources-configured case."""

    def __init__(
        self,
        *,
        name: str = "default",
        repo: str = "acme/widget",
        body: str = "issue body",
        comments: list[str] | None = None,
        by_url: dict[str, PmItem] | None = None,
        fail_urls: set[str] | None = None,
    ) -> None:
        self.name = name
        self.repo = repo
        self.body = body
        self.comments = comments or []
        self.by_url = by_url or {}
        self.fail_urls = fail_urls or set()
        self.fetched: list[str] = []

    def parse(self, token: str) -> PmPointer:
        prefix, sep, ref = token.partition(":")
        if not sep or prefix != self.name or not ref.isdigit():
            raise UnknownSource(f"{token!r} is not a {self.name!r} source token")
        return PmPointer(provider="github", url=f"http://forge.local/repos/{self.repo}/issues/{ref}")

    def fetch(self, pointer: PmPointer) -> PmItem:
        self.fetched.append(pointer.url)
        if pointer.url in self.fail_urls:
            raise PmSourceError(f"forge unreachable for {pointer.url}")
        if pointer.url in self.by_url:
            return self.by_url[pointer.url]
        return PmItem(body=self.body, comments=list(self.comments))

    def label(self, pointer: PmPointer) -> str | None:
        match = _ISSUE_RE.search(pointer.url)
        return f"{self.name}#{match['number']}" if match is not None else None

    def web_url(self, pointer: PmPointer) -> str | None:
        return pointer.url if _ISSUE_RE.search(pointer.url) is not None else None

    def branch_url(self, repo: str, branch_name: str) -> str | None:
        return f"http://forge.local/{repo}/tree/{branch_name}"

    def owns(self, pointer: PmPointer) -> bool:
        return _repo_of(pointer.url) == self.repo


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
    pm: PmSourceRegistry
    clock: FixedClock
    events: EventBroker = field(default_factory=EventBroker)


def build_hub(
    tmp_path: Path,
    *,
    forge: FakeForge | None = None,
    pm: dict[str, FakePmSource] | None = None,
    base_branch: str = "main",
) -> HubHarness:
    """A migrated, fully-wired hub over ``tmp_path`` with fake external seams.

    ``pm`` is ``{name: FakePmSource}`` — the same name-keyed shape the real
    :func:`~blizzard.hub.pm.internal.factory.build_pm_registry` produces (D-106);
    defaults to one entry so the common single-source case needs no test churn.
    ``None`` defaults to one source; an explicit ``pm={}`` is a legal, deliberately
    **empty** registry (D-106) — ``or`` would silently coerce that back to the default,
    which is what made the empty-registry path unreachable through this harness."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    config = HubConfig(root=tmp_path, db_url=db_url)
    migration_runner(config).upgrade("head")

    forge = forge or FakeForge()
    pm_registry = PmSourceRegistry(pm if pm is not None else {"default": FakePmSource()})
    clock = FixedClock(datetime(2026, 7, 13, tzinfo=UTC))
    events = EventBroker()
    engine = create_engine_from_url(db_url)
    services = build_services(engine, forge=forge, events=events, pm=pm_registry, clock=clock, base_branch=base_branch)
    app = create_app(config, services=services)
    return HubHarness(
        client=TestClient(app), services=services, forge=forge, pm=pm_registry, clock=clock, events=events
    )


def write_pm_sources(hub_dir: Path, sources: Sequence[PmSourceConfig]) -> HubConfig:
    """Declare ``[[pm_source]]`` entries on an already-``init``ed hub runtime dir (D-106/D-107).

    Every upper-tier fixture (``tests/e2e``, ``tests/crash``, ``tests/journey``,
    ``tests/service``) runs ``blizzard hub init`` from its own subprocess-driven support
    code and then ingests — since Phase 2 the ingest route 422s a pointer no configured
    source claims, so each fixture must declare its sources or its own ingests fail.
    Round-trips through :meth:`~blizzard.hub.config.HubConfig.load` ->
    ``dataclasses.replace`` -> :meth:`~blizzard.hub.config.HubConfig.to_toml` — the same
    shape ``tests/crash/support.py::write_runner_config`` uses for the runner config, a
    fixed point verified against a real ``hub init`` hub."""
    config = HubConfig.load(hub_dir)
    config = replace(config, pm_sources=tuple(sources))
    config.config_path.write_text(config.to_toml())
    return config


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
