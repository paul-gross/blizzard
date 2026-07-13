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
from blizzard.hub.delivery.forge import IForgeDelivery, LandingDisposition, LandingRequest, LandingResult
from blizzard.hub.domain.work import PmPointer
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.pm.source import IPmSource, PmItem
from blizzard.hub.runtime import migration_runner


class FakeForge:
    """An in-process :class:`IForgeDelivery` — records lands, arms conflicts by repo."""

    def __init__(self) -> None:
        self.landed: list[LandingRequest] = []
        self.conflict_repos: set[str] = set()

    def land(self, request: LandingRequest) -> LandingResult:
        if request.repo in self.conflict_repos:
            return LandingResult(disposition=LandingDisposition.CONFLICT, landed_commit=None, detail="armed conflict")
        self.landed.append(request)
        return LandingResult(disposition=LandingDisposition.LANDED, landed_commit=f"merged-{request.commit_hash}")

    def open_pr(self, request: LandingRequest):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def check_pr(self, handle):  # type: ignore[no-untyped-def]
        raise NotImplementedError


def _conforms_fake_forge(x: FakeForge) -> IForgeDelivery:
    return x


class FakePmSource:
    """An in-process :class:`IPmSource` — canned body + comments per pointer URL."""

    def __init__(self, *, body: str = "issue body", comments: list[str] | None = None) -> None:
        self.body = body
        self.comments = comments or []
        self.fetched: list[str] = []

    def fetch(self, pointer: PmPointer) -> PmItem:
        self.fetched.append(pointer.url)
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

    @app.post("/repos/{owner}/{repo}/pulls", status_code=201)
    def create_pull(owner: str, repo: str, body: dict) -> dict:
        number = int(state["next_pull"])  # type: ignore[arg-type]
        state["next_pull"] = number + 1
        state["pulls"][number] = {"head": body["head"], "base": body["base"]}  # type: ignore[index]
        return {
            "number": number,
            "html_url": f"http://forge/{owner}/{repo}/pull/{number}",
            "head": {"ref": body["head"]},
        }

    @app.put("/repos/{owner}/{repo}/pulls/{number}/merge")
    def merge_pull(owner: str, repo: str, number: int, body: dict) -> JSONResponse:
        head = state["pulls"].get(number, {}).get("head")  # type: ignore[union-attr]
        if head in conflict:
            return JSONResponse(status_code=409, content={"message": "not mergeable"})
        return JSONResponse(
            status_code=200, content={"sha": f"merged-{body.get('sha')}", "merged": True, "message": "ok"}
        )

    return TestClient(app)


@dataclass
class HubHarness:
    """A wired hub app plus the collaborators a test drives and asserts against."""

    client: TestClient
    services: HubServices
    forge: FakeForge
    pm: FakePmSource
    clock: FixedClock
    events: EventBroker = field(default_factory=EventBroker)


def build_hub(tmp_path: Path, *, forge: FakeForge | None = None, pm: FakePmSource | None = None) -> HubHarness:
    """A migrated, fully-wired hub over ``tmp_path`` with fake external seams."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    config = HubConfig(root=tmp_path, db_url=db_url)
    migration_runner(config).upgrade("head")

    forge = forge or FakeForge()
    pm = pm or FakePmSource()
    clock = FixedClock(datetime(2026, 7, 13, tzinfo=UTC))
    events = EventBroker()
    engine = create_engine_from_url(db_url)
    services = build_services(engine, forge=forge, events=events, pm_source=pm, clock=clock)
    app = create_app(config, services=services)
    return HubHarness(client=TestClient(app), services=services, forge=forge, pm=pm, clock=clock, events=events)


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
