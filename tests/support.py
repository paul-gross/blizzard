"""Shared component-test scaffolding — a fully-wired hub over a tmp sqlite store.

Builds the store-backed ``host`` composition with the work-item read seam replaced by
:class:`FakeWorkSource` (``bzh:pluggable-seams``) and a clock the test can advance
(``bzh:injected-clock``). A deliver hub node's script talks HTTP directly, so arm
:class:`FakeHubCommandRunner`."""

from __future__ import annotations

import functools
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy import insert as sa_insert

from blizzard.auth_core import Role
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.ids import USER_PREFIX, Id
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.app import create_app
from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.internal.user_repository import UserRepository
from blizzard.hub.auth.models import User
from blizzard.hub.auth.oauth.provider import IOAuthProvider
from blizzard.hub.auth.oauth.registry import OAuthProviderRegistry
from blizzard.hub.composition import HubServices, build_services
from blizzard.hub.config import (
    AUTH_MODE_NONE,
    AUTH_MODE_OAUTH,
    PRODUCES_WARN,
    ROUTE_TOKEN_WARN,
    RUNNER_AUTH_WARN,
    AuthConfig,
    HubConfig,
    WorkSourceConfig,
)
from blizzard.hub.delivery.command_runner import CommandResult, IHubCommandRunner
from blizzard.hub.delivery.workdir import IHubWorkdir
from blizzard.hub.domain.delete import DeleteService
from blizzard.hub.domain.findings import FindingExitService
from blizzard.hub.domain.garden_proposal_resolution import GardenProposalDeliveryResolution
from blizzard.hub.domain.graph import Edge, Graph, Node
from blizzard.hub.domain.transcripts import TranscriptCaps
from blizzard.hub.domain.work import (
    Chunk,
    IWriteWorkItemRepository,
    WorkItemAuthor,
    WorkItemRecord,
    WorkRef,
)
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.store.internal.chunk_store import ChunkStore
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.garden_proposal_closure_store import GardenProposalClosureStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.system_artifacts import PackagedSystemArtifacts
from blizzard.hub.work_sources.annotator import IWorkAnnotator, WorkAnnotateError, WorkStatusMarker
from blizzard.hub.work_sources.closer import IWorkCloser, WorkCloseError, WorkItemGoneError
from blizzard.hub.work_sources.editor import IWorkEditor
from blizzard.hub.work_sources.internal.hub_work_source import seat_hub_work_source
from blizzard.hub.work_sources.registry import WorkSourceRegistry
from blizzard.hub.work_sources.source import IWorkSource, WorkItem, WorkSourceError

_GRAPH_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def hub_store_connections(engine: Engine) -> HubStoreConnections:
    """The ``hub/store/internal/`` seam (issue #413) every adapter test wires over its
    own migrated engine — one helper so the 13 adapters' test files construct it
    identically."""
    return HubStoreConnections(engine, HubStoreErrorFactory(get_logger("test")))


def make_graph(
    graph_id: str,
    name: str,
    *,
    entry_node_id: str = "nd_entry",
    nodes: list[Node] | None = None,
    edges: list[Edge] | None = None,
    created_at: datetime = _GRAPH_T0,
) -> Graph:
    """A minimal :class:`Graph` — defaults to no nodes/edges, a fixed ``created_at``."""
    return Graph(
        graph_id=graph_id,
        name=name,
        entry_node_id=entry_node_id,
        nodes=nodes if nodes is not None else [],
        edges=edges if edges is not None else [],
        created_at=created_at,
    )


class FakeHubCommandRunner:
    """An in-process :class:`IHubCommandRunner` — scripted results by command, in order.

    ``script`` queues :class:`CommandResult`\\ s per command (popped in order, repeating
    the last once exhausted); ``calls`` records every invocation for assertion."""

    def __init__(self, *, default: CommandResult | None = None) -> None:
        self.script: dict[str, list[CommandResult]] = {}
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.default = default or CommandResult(exit_code=0, stdout="", stderr="")
        self.before_run: Callable[[str], None] | None = None

    def arm(self, command: str, *results: CommandResult) -> None:
        self.script.setdefault(command, []).extend(results)

    def run(self, *, command: str, cwd: str, env: dict[str, str]) -> CommandResult:
        self.calls.append((command, cwd, env))
        if self.before_run is not None:
            self.before_run(command)
        queue = self.script.get(command)
        if queue:
            return queue.pop(0) if len(queue) > 1 else queue[0]
        return self.default


class FakeHubWorkdir:
    """An in-process :class:`IHubWorkdir` — a plain in-memory chunk-id -> path map."""

    def __init__(self) -> None:
        self.ensured: list[str] = []
        self.expired: list[str] = []
        self._paths: dict[str, str] = {}

    def ensure(self, chunk_id: str) -> str:
        self.ensured.append(chunk_id)
        return self._paths.setdefault(chunk_id, f"/tmp/fake-hub-workdir/{chunk_id}")

    def expire(self, chunk_id: str) -> None:
        self.expired.append(chunk_id)
        self._paths.pop(chunk_id, None)

    def list_orphans(self) -> list[str]:
        return list(self._paths)


def _conforms_fake_hub_command_runner(x: FakeHubCommandRunner) -> IHubCommandRunner:
    return x


def _conforms_fake_hub_workdir(x: FakeHubWorkdir) -> IHubWorkdir:
    return x


class FakeWorkSource:
    """An in-process :class:`IWorkSource` — canned title + body + comments per pointer ref.

    Keyed on ``pointer.ref`` rather than a URL; ``by_ref``/``fail_refs`` override or fail
    specific refs to exercise per-pointer forge-failure degradation."""

    def __init__(
        self,
        *,
        name: str = "default",
        repo: str = "acme/widget",
        title: str = "issue title",
        body: str = "issue body",
        comments: list[str] | None = None,
        by_ref: dict[str, WorkItem] | None = None,
        fail_refs: set[str] | None = None,
    ) -> None:
        self.name = name
        self.repo = repo
        self.title = title
        self.body = body
        self.comments = comments or []
        self.by_ref = by_ref or {}
        self.fail_refs = fail_refs or set()
        self.fetched: list[str] = []

    def parse(self, token: str) -> WorkRef | None:
        """``{name}:{ref}`` or ``{name}#{ref}``; ``None`` otherwise — no URL grammar, and
        any non-empty ``ref`` shape is accepted."""
        for sep_char in (":", "#"):
            prefix, sep, ref = token.partition(sep_char)
            if sep and prefix == self.name and ref:
                return WorkRef(source=self.name, ref=ref)
        return None

    def fetch(self, pointer: WorkRef) -> WorkItem:
        self.fetched.append(pointer.ref)
        if pointer.ref in self.fail_refs:
            raise WorkSourceError(f"forge unreachable for {pointer.ref}")
        if pointer.ref in self.by_ref:
            return self.by_ref[pointer.ref]
        return WorkItem(body=self.body, title=self.title, comments=list(self.comments))

    def label(self, pointer: WorkRef) -> str | None:
        return f"{self.name}#{pointer.ref}"

    def web_url(self, pointer: WorkRef) -> str | None:
        return f"http://forge.local/{self.repo}/issues/{pointer.ref}"

    def branch_url(self, repo: str, branch_name: str) -> str | None:
        return f"http://forge.local/{repo}/tree/{branch_name}"


def _conforms_fake_work_source(x: FakeWorkSource) -> IWorkSource:
    return x


class FakeAnnotator:
    """An in-process :class:`IWorkAnnotator` — an in-memory ``{ref: {markers}}`` map,
    plus call logs a test asserts against. ``fail_refs`` raises
    :class:`WorkAnnotateError` for a ref's own :meth:`set_status`/:meth:`clear_status`,
    mirroring :class:`FakeWorkSource`'s own per-pointer failure knob."""

    def __init__(
        self,
        *,
        initial: dict[WorkRef, set[WorkStatusMarker]] | None = None,
        fail_refs: set[str] | None = None,
    ) -> None:
        self._marks: dict[WorkRef, set[WorkStatusMarker]] = {
            ref: set(markers) for ref, markers in (initial or {}).items()
        }
        self.fail_refs = fail_refs or set()
        self.set_calls: list[tuple[WorkRef, WorkStatusMarker]] = []
        self.clear_calls: list[WorkRef] = []

    def set_status(self, pointer: WorkRef, marker: WorkStatusMarker) -> None:
        if pointer.ref in self.fail_refs:
            raise WorkAnnotateError(f"boom setting {marker.value} on {pointer.ref}")
        self.set_calls.append((pointer, marker))
        self._marks[pointer] = {marker}

    def clear_status(self, pointer: WorkRef) -> None:
        if pointer.ref in self.fail_refs:
            raise WorkAnnotateError(f"boom clearing {pointer.ref}")
        self.clear_calls.append(pointer)
        self._marks.pop(pointer, None)

    def marked_refs(self) -> dict[WorkRef, frozenset[WorkStatusMarker]]:
        return {ref: frozenset(markers) for ref, markers in self._marks.items() if markers}


def _conforms_fake_annotator(x: FakeAnnotator) -> IWorkAnnotator:
    return x


class FakeCloser:
    """An in-process :class:`IWorkCloser` — an in-memory ``{ref: outcome}`` map plus
    a call log a test asserts against. ``gone_refs`` raises
    :class:`WorkItemGoneError`; ``fail_refs`` raises a bare :class:`WorkCloseError`."""

    def __init__(self, *, gone_refs: set[str] | None = None, fail_refs: set[str] | None = None) -> None:
        self.gone_refs = gone_refs or set()
        self.fail_refs = fail_refs or set()
        self.closed: list[WorkRef] = []

    def close(self, pointer: WorkRef) -> None:
        if pointer.ref in self.gone_refs:
            raise WorkItemGoneError(f"{pointer.ref} no longer exists")
        if pointer.ref in self.fail_refs:
            raise WorkCloseError(f"boom closing {pointer.ref}")
        self.closed.append(pointer)


def _conforms_fake_closer(x: FakeCloser) -> IWorkCloser:
    return x


class _OmitTitle:
    """The sentinel a test uses to make :func:`github_double` omit ``title`` from the payload."""

    def __repr__(self) -> str:
        return "OMIT_TITLE"


OMIT_TITLE = _OmitTitle()
"""Sentinel — a forge payload with no ``title`` key at all (real GitHub never sends this)."""


def github_double(
    *,
    conflict_branches: set[str] | None = None,
    issues: dict[str, dict] | None = None,
    pull_numbers: set[int] | None = None,
) -> TestClient:
    """A tiny GitHub-shaped forge double for the real HTTP adapters.

    Exercises the adapter HTTP shaping against a minimal GitHub-REST-v3 surface — issue
    read + comments, PR create + merge, and label routes — without coupling this repo to
    ``blizzard-mock`` as a dev dependency. Wrapped in a ``TestClient``."""
    from fastapi.responses import JSONResponse

    conflict = conflict_branches or set()
    issue_store = issues or {}
    app = FastAPI()
    state: dict[str, object] = {
        "next_pull": 1,
        "pulls": {},
        "repo_labels": {},
        "issue_labels": {},
        "pr_numbers": set(pull_numbers or set()),
    }

    @app.get("/repos/{owner}/{repo}/issues/{number}")
    def get_issue(owner: str, repo: str, number: int) -> dict:
        key = f"{owner}/{repo}#{number}"
        data = issue_store.get(key, {"body": f"issue {number}", "comments": []})
        payload: dict[str, object] = {"number": number, "body": data["body"]}
        # A double laxer than the forge it stands for would hide bugs, so ``title`` is
        # present by default and a test opts into the degenerate shapes explicitly.
        title = data.get("title", f"issue {number}")
        if title is not OMIT_TITLE:
            payload["title"] = title
        return payload

    @app.patch("/repos/{owner}/{repo}/issues/{number}")
    def update_issue_state(owner: str, repo: str, number: int, body: dict) -> JSONResponse:
        if (forbidden := _forbidden_if_armed()) is not None:
            return forbidden
        # A number the double's fixed issue store doesn't know about is the "gone"
        # case a closer must surface distinctly from a generic failure.
        issue_state: dict[str, dict] = state.setdefault("issue_state", {})  # type: ignore[assignment]
        key = f"{owner}/{repo}#{number}"
        if key not in issue_store and key not in issue_state:
            return JSONResponse(status_code=404, content={"message": "Not Found"})
        issue_state[key] = {"state": body["state"], "state_reason": body.get("state_reason")}
        return JSONResponse(status_code=200, content={"number": number, **issue_state[key]})

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

    def _forbidden_if_armed() -> JSONResponse | None:
        """The ``forbidden`` lever a test arms via ``client.forge_state["forbidden"]
        = True`` — an insufficient-scope 403 (the rate-limit 403 is not modelled)."""
        if state.get("forbidden"):
            return JSONResponse(status_code=403, content={"message": "Resource not accessible by integration"})
        return None

    @app.post("/repos/{owner}/{repo}/labels")
    def create_repo_label(owner: str, repo: str, body: dict) -> JSONResponse:
        if (forbidden := _forbidden_if_armed()) is not None:
            return forbidden
        repo_labels = state["repo_labels"].setdefault(f"{owner}/{repo}", set())  # type: ignore[union-attr]
        name = body["name"]
        if name in repo_labels:
            return JSONResponse(status_code=422, content={"message": "already_exists"})
        repo_labels.add(name)
        state.setdefault("repo_label_colors", {})[name] = body.get("color")  # type: ignore[union-attr]
        return JSONResponse(status_code=201, content={"name": name})

    @app.post("/repos/{owner}/{repo}/issues/{number}/labels")
    def add_issue_labels(owner: str, repo: str, number: int, body: list[str]) -> JSONResponse:
        if (forbidden := _forbidden_if_armed()) is not None:
            return forbidden
        # `label_add_forbidden` fails only this route; the broad `forbidden` lever can't
        # isolate an add failure since it trips the bootstrap POST first.
        if state.get("label_add_forbidden"):
            return JSONResponse(status_code=403, content={"message": "Resource not accessible by integration"})
        key = f"{owner}/{repo}#{number}"
        issue_labels = state["issue_labels"].setdefault(key, set())  # type: ignore[union-attr]
        issue_labels.update(body)
        return JSONResponse(status_code=200, content=[{"name": name} for name in sorted(issue_labels)])

    @app.delete("/repos/{owner}/{repo}/issues/{number}/labels/{name}")
    def remove_issue_label(owner: str, repo: str, number: int, name: str) -> JSONResponse:
        if (forbidden := _forbidden_if_armed()) is not None:
            return forbidden
        key = f"{owner}/{repo}#{number}"
        issue_labels = state["issue_labels"].setdefault(key, set())  # type: ignore[union-attr]
        if name not in issue_labels:
            return JSONResponse(status_code=404, content={"message": "Label does not exist"})
        issue_labels.discard(name)
        return JSONResponse(status_code=200, content=[{"name": n} for n in sorted(issue_labels)])

    @app.get("/repos/{owner}/{repo}/issues")
    def list_issues(
        owner: str, repo: str, request: Request, labels: str | None = None, per_page: int = 30, page: int = 1
    ) -> JSONResponse:
        if (forbidden := _forbidden_if_armed()) is not None:
            return forbidden
        repo_key = f"{owner}/{repo}"
        wanted = set(labels.split(",")) if labels else None
        issue_labels_map: dict[str, set[str]] = state["issue_labels"]  # type: ignore[assignment]
        pr_numbers: set[int] = state["pr_numbers"]  # type: ignore[assignment]
        numbers = sorted(
            int(key.rpartition("#")[2])
            for key, names in issue_labels_map.items()
            if key.startswith(f"{repo_key}#") and (wanted is None or wanted <= names)
        )
        start = (page - 1) * per_page
        page_numbers = numbers[start : start + per_page]
        items: list[dict] = []
        for n in page_numbers:
            item: dict[str, object] = {
                "number": n,
                "labels": [{"name": name} for name in sorted(issue_labels_map.get(f"{repo_key}#{n}", set()))],
            }
            if n in pr_numbers:
                item["pull_request"] = {"url": f"http://forge/{repo_key}/pulls/{n}"}
            items.append(item)
        headers = {}
        if start + per_page < len(numbers):
            next_url = str(request.url.include_query_params(page=page + 1))
            headers["Link"] = f'<{next_url}>; rel="next"'
        return JSONResponse(status_code=200, content=items, headers=headers)

    client = TestClient(app)
    client.forge_state = state  # type: ignore[attr-defined]  # tests flip PR fate (e.g. close-without-merge)
    return client


def forge_state(double: TestClient) -> dict[str, object]:
    """Typed accessor for a :func:`github_double`'s mutable state dict — a test
    seeds/reads ``issue_labels``/``repo_labels``/``pr_numbers``/``forbidden``/
    ``label_add_forbidden`` directly."""
    return double.forge_state  # type: ignore[attr-defined]


@dataclass
class HubHarness:
    """A wired hub app plus the collaborators a test drives and asserts against."""

    client: TestClient
    services: HubServices
    work_sources: WorkSourceRegistry
    clock: FixedClock
    engine: Engine
    events: EventBroker = field(default_factory=EventBroker)
    #: The wired app, so a test can build a second ``TestClient`` with a different peer
    #: address — the forwarded-header trust tests (issue #130) need a concrete IP peer.
    app: FastAPI | None = None


def build_hub(
    tmp_path: Path,
    *,
    work_sources: dict[str, FakeWorkSource] | None = None,
    base_branch: str = "main",
    hub_command_runner: IHubCommandRunner | None = None,
    hub_workdir: IHubWorkdir | None = None,
    forge_owner: str | None = None,
    runner_auth_mode: str = RUNNER_AUTH_WARN,
    route_token_mode: str = ROUTE_TOKEN_WARN,
    produces_mode: str = PRODUCES_WARN,
    follow_latest: bool = False,
    auth_mode: str = AUTH_MODE_NONE,
    superuser: str | None = None,
    oauth_providers: dict[str, IOAuthProvider] | None = None,
    trusted_proxies: Sequence[str] = (),
    transcript_caps: TranscriptCaps | None = None,
    system_artifacts: PackagedSystemArtifacts | None = None,
) -> HubHarness:
    """A migrated, fully-wired hub over ``tmp_path`` with fake external seams.

    ``work_sources=None`` defaults to one fake source; an explicit ``work_sources={}`` is
    a legal, deliberately **empty** registry — ``or`` would silently coerce it back to the
    default. ``hub_command_runner``/``hub_workdir`` left ``None`` wire real adapters."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    config = HubConfig(
        root=tmp_path,
        db_url=db_url,
        runner_auth_mode=runner_auth_mode,
        route_token_mode=route_token_mode,
        produces_mode=produces_mode,
        follow_latest=follow_latest,
        auth=AuthConfig(mode=auth_mode, superuser=superuser),
        trusted_proxies=tuple(trusted_proxies),
    )
    migration_runner(config).upgrade("head")
    engine = create_engine_from_url(db_url)

    built_sources: dict[str, IWorkSource] = dict(
        work_sources if work_sources is not None else {"default": FakeWorkSource()}
    )
    clock = FixedClock(datetime(2026, 7, 13, tzinfo=UTC))
    user_store = UserRepository(engine, RepoErrorFactory(get_logger("blizzard.hub.auth")))
    editors: dict[str, IWorkEditor] = {}
    # The built-in `hub` source is seated as a closer unconditionally (issue #360),
    # mirroring `WorkSourceEntry.registry`'s production wiring.
    closers: dict[str, IWorkCloser] = {}
    # Constructed once here, ahead of both the work-source registry and `build_services`
    # below — mirrors `build_hosted_app`'s own wiring (issue #364).
    claim_lock = threading.Lock()
    store_connections = hub_store_connections(engine)
    work_item_store = WorkItemStore(store_connections)
    delete_service = DeleteService(
        chunks=ChunkStore(store_connections, clock), items=work_item_store, clock=clock, claim_lock=claim_lock
    )
    finding_store = FindingStore(store_connections)
    finding_exit = FindingExitService(repo=finding_store, clock=clock)
    garden_proposal_resolution = GardenProposalDeliveryResolution(
        closures=GardenProposalClosureStore(store_connections),
        proposals=GardenProposalStore(store_connections),
        findings=finding_store,
        exits=finding_exit,
    )
    seat_hub_work_source(
        built_sources,
        editors,
        closers,
        store=store_connections,
        clock=clock,
        users=user_store,
        items=work_item_store,
        delete=delete_service,
        resolution=garden_proposal_resolution,
    )
    work_source_registry = WorkSourceRegistry(built_sources, closers=closers, editors=editors)
    events = EventBroker()
    services = build_services(
        engine,
        events=events,
        work_sources=work_source_registry,
        claim_lock=claim_lock,
        work_item_store=work_item_store,
        delete=delete_service,
        finding_store=finding_store,
        finding_exit=finding_exit,
        clock=clock,
        users=user_store,
        base_branch=base_branch,
        hub_command_runner=hub_command_runner,
        hub_workdir=hub_workdir,
        hub_workdir_root=tmp_path / "hub_workdirs",
        hub_marker_callback_base_url="http://testserver",
        forge_owner=forge_owner,
        oauth_registry=OAuthProviderRegistry(oauth_providers) if oauth_providers is not None else None,
        # The IdP signing-key lifecycle (issue #95) — wired only under `oauth`, mirroring
        # `hub/app.py`'s own `build_hosted_app` gating exactly.
        signing_keys_dir=(tmp_path / "auth" / "signing-keys") if auth_mode == AUTH_MODE_OAUTH else None,
        trusted_proxies=TrustedProxies.parse(config.trusted_proxies),
        transcript_caps=transcript_caps,
        system_artifacts=system_artifacts,
    )
    app = create_app(config, services=services)
    client = TestClient(app)
    # Warm FastAPI's per-router route-resolution cache: it lazily caches routes on first
    # use, which is thread-unsafe under the component tier's OS-thread races.
    client.get("/api/_route_cache_warm")
    return HubHarness(
        client=client,
        services=services,
        work_sources=work_source_registry,
        clock=clock,
        engine=engine,
        events=events,
        app=app,
    )


def write_work_sources(hub_dir: Path, sources: Sequence[WorkSourceConfig]) -> HubConfig:
    """Declare ``[[work_source]]`` entries on an already-``init``ed hub runtime dir.

    Every upper-tier fixture that runs ``blizzard hub init`` and then ingests must
    declare its sources through this, or its own ingests fail — a load/replace/save
    round trip through :class:`~blizzard.hub.config.HubConfig`."""
    config = HubConfig.load(hub_dir)
    config = replace(config, work_sources=tuple(sources))
    config.config_path.write_text(config.to_toml())
    return config


def daemon_log_sink(path: Path) -> IO[str]:
    """An append-mode file for a spawned daemon's merged stdout/stderr.

    A long-lived daemon must NEVER get ``stdout=PIPE`` (``bzh:daemon-stdout-to-file``):
    nothing here drains it, so the daemon wedges once the pipe buffer fills, surfacing
    as an unrelated timeout far from the cause (issue #145)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("a", buffering=1)


def read_daemon_log(path: Path | None) -> str:
    """A spawned daemon's log text, or a legible stand-in — the early-exit diagnostic.

    Total by design: a daemon that died before its log file existed must still produce a
    message naming what happened, so a missing/unreadable file degrades to a note rather
    than masking the failure with an :class:`OSError` of its own."""
    if path is None:
        return "<no log file>"
    try:
        return path.read_text()
    except OSError as exc:  # pragma: no cover - defensive, see docstring
        return f"<log {path} unreadable: {exc}>"


@functools.lru_cache(maxsize=1)
def shared_daemon_log_dir() -> Path:
    """The per-process fallback log directory for daemons spawned with no runtime dir.

    The mock fleet's daemons own no runtime directory to put a log beside, and threading
    one through their ~40 call sites buys nothing a single well-named directory does not.
    Created once per pytest process and reused."""
    return Path(tempfile.mkdtemp(prefix="blizzard-daemon-logs-"))


def parse_sse_frames(text: str) -> list[dict[str, str]]:
    """Parse an ``text/event-stream`` payload into ``[{id, event, data}]`` dicts.

    Reserved comment lines (``:``-prefixed) and keepalives are skipped; a blank line
    terminates one frame.
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

    Starlette's ``TestClient`` buffers a whole response body, so it cannot consume an
    infinite live stream incrementally. Instead this drives the route's async generator
    directly with a request that reports itself disconnected, emitting the replay tail."""
    from blizzard.hub.api.events import _RESERVED_COMMENT, Cursor, Stream

    class _DisconnectedRequest:
        async def is_disconnected(self) -> bool:
            return True

    chunks: list[bytes] = []
    stream = Stream(broker, _DisconnectedRequest(), Cursor(last_event_id), _RESERVED_COMMENT)  # type: ignore[arg-type]
    async for chunk in stream.frames():
        chunks.append(chunk)
    return parse_sse_frames(b"".join(chunks).decode())


def emitted_events(hub: HubHarness, *, since: int = 0) -> list[dict[str, str]]:
    """The typed events the hub published after ``since`` — the broker's replay tail.

    Asserting on it asserts SSE emission without the buffering-transport limitation
    :func:`drain_stream` describes. Each dict carries ``id``, ``event``, ``data``.
    """
    return [{"id": str(e.id), "event": e.type, "data": e.data} for e in hub.events.replay_since(since)]


def pointer_token(pointer: dict) -> str:
    """A ``{source, ref}`` pointer dict's own ``{source}:{ref}`` ingest token —
    the request-side shape a test builds from the same dict it asserts the response
    (``{source, ref, label, web_url}``) against."""
    return f"{pointer['source']}:{pointer['ref']}"


def ingest(hub: HubHarness, pointers: list[dict], *, promote: bool = True) -> str:
    """Ingest ``pointers`` (as ``{source, ref}`` dicts) into one chunk and, by default,
    promote it to ready — each dict converts to its ``{source}:{ref}`` ingest token.

    Pass ``promote=False`` to assert the not-ready resting state a bare ingest leaves."""
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(p) for p in pointers]})
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]
    if promote:
        promoted = hub.client.post(f"/api/chunks/{chunk_id}/promote")
        assert promoted.status_code == 202, promoted.text
    return chunk_id


def write_chunk_pause_facts(tmp_path: Path, chunk_id: str, *facts: tuple[bool, datetime]) -> None:
    """Append ``chunk_pause_facts`` rows for ``chunk_id``, in argument order (issue #46).

    Not a stand-in for the pause route: this exists for the one thing it cannot express
    — **arbitrary ``set_at`` values**, since the route stamps a single ``clock.now()``.
    Each tuple is ``(paused, set_at)``; write order is the newest-wins order."""
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
    with engine.begin() as conn:
        for paused, set_at in facts:
            conn.execute(
                sa_insert(schema.chunk_pause_facts).values(
                    chunk_id=chunk_id, paused=paused, set_at=set_at, set_by="operator"
                )
            )


def seed_user(
    hub: HubHarness, *, username: str, role: Role, email: str | None = None, display_name: str | None = None
) -> User:
    """Insert one ``users`` row directly (a raw-write test helper, mirrors
    ``write_chunk_pause_facts``) and return the domain object.

    No login mechanism exists yet (issue #91), so a test wanting a ``ResolvedIdentity``
    seeds the row directly rather than through a route."""
    user = User(
        user_id=Id.mint(USER_PREFIX, hub.clock).value,
        username=username,
        display_name=display_name or username,
        email=email,
        role=role,
        created_at=hub.clock.now(),
    )
    with hub.engine.begin() as conn:
        conn.execute(
            sa_insert(schema.users).values(
                id=user.user_id,
                username=user.username,
                display_name=user.display_name,
                email=user.email,
                role=user.role.value,
                created_at=user.created_at,
            )
        )
    return user


def seed_session(hub: HubHarness, user: User) -> str:
    """Mint a real session for ``user`` via ``AuthService.mint_session`` (#92) and return
    the plaintext session id — a test sets this as the ``bz_session`` cookie or an
    ``Authorization: Bearer`` header."""
    plaintext, _ = hub.services.auth.mint_session(user)
    return plaintext


def assert_utc_iso(value: object) -> None:
    """Assert ``value`` is a literal ISO-8601 string carrying an explicit UTC offset.

    Pins the wire **bytes**, not a parsed-then-compared value (issue #28,
    ``bzh:utc-instants``): a naive string re-parses fine on the same box that emitted it,
    so only the literal trailing designator (``+00:00`` / ``Z``) catches the bug."""
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


def report_lease(
    hub: HubHarness, chunk_id: str, *, epoch: int, seq: int, runner_id: str = "r1", route_token: str | None = None
) -> dict:
    """Report a runner-minted ``lease.minted`` fact through POST /events.

    A component test calls this first so the hub knows the chunk's latest epoch.
    ``route_token`` (issue #84b) rides the payload; ``None`` omits it, matching a caller
    that never claimed under the plaintext."""
    payload: dict[str, object] = {"chunk_id": chunk_id, "epoch": epoch}
    if route_token is not None:
        payload["route_token"] = route_token
    resp = hub.client.post(
        "/api/fleet/events",
        json={"runner_id": runner_id, "facts": [{"seq": seq, "kind": "lease.minted", "payload": payload}]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# Migration-test scaffolding: `graphs`/`chunks` carry no revision-pinned shape, so these
# seeds are shared. A revision's own frozen table-under-test must NOT move here.

_GRAPHS = sa.Table(
    "graphs",
    sa.MetaData(),
    sa.Column("graph_id", sa.String, primary_key=True),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("entry_node_id", sa.String, nullable=False),
    sa.Column("definition_yaml", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False),
)

_CHUNKS = sa.Table(
    "chunks",
    sa.MetaData(),
    sa.Column("chunk_id", sa.String, primary_key=True),
    sa.Column("graph_id", sa.String, nullable=False),
    sa.Column("minted_at", sa.DateTime, nullable=False),
)


def migrate_to(tmp_path: Path, revision: str) -> tuple[MigrationRunner, Engine]:
    """A hub store migrated to ``revision``, ready for a test's own revision-pinned seed
    rows. The returned runner is the same handle a test upgrades onward from (e.g. to
    ``"head"``) once its seed is in place."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(revision)
    return runner, create_engine_from_url(db_url)


def seed_graph(conn: sa.Connection, graph_id: str, *, at: datetime) -> None:
    """Seed one ``graphs`` parent row — the FK a seeded chunk needs, at any revision."""
    conn.execute(
        sa.insert(_GRAPHS).values(graph_id=graph_id, name="g", entry_node_id="nd_1", definition_yaml="", created_at=at)
    )


def seed_chunk(conn: sa.Connection, chunk_id: str, *, graph_id: str, at: datetime) -> None:
    """Seed one ``chunks`` parent row — the FK a seeded route/pointer/etc. needs."""
    conn.execute(sa.insert(_CHUNKS).values(chunk_id=chunk_id, graph_id=graph_id, minted_at=at))


def seed_work_item(
    store: IWriteWorkItemRepository,
    *,
    source: str = "hub",
    graph_id: str,
    title: str = "t",
    body: str = "b",
    author: WorkItemAuthor,
    stated_priority: str | None = None,
    at: datetime,
) -> WorkItemRecord:
    """Seed one hub-owned work item plus its resting chunk, mirroring production's own
    two-step mint (``WorkItemEditService.create``, blizzard#359) — there is no chunkless
    filing path to seed around. Callers still seed ``graph_id``'s own row first
    (``seed_graph``); this only seeds the item and its chunk."""
    ref = store.allocate_ref(source)
    pointer = WorkRef(source=source, ref=ref)
    chunk = Chunk(chunk_id=f"ch_{ref}", graph_id=graph_id, work_refs=[pointer], minted_at=at)
    return store.create_with_chunk(
        pointer=pointer, title=title, body=body, author=author, stated_priority=stated_priority, at=at, chunk=chunk
    )


def count_queries(engine: Engine, fn: Callable[[], object]) -> int:
    """How many statements ``fn`` issues on ``engine`` — what a bulk-read test asserts is
    flat as the fleet grows, rather than growing per chunk."""
    count = 0

    def before_cursor_execute(*_: object) -> None:
        nonlocal count
        count += 1

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
    return count
