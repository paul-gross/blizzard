"""Shared component-test scaffolding — a fully-wired hub over a tmp sqlite store.

Builds the store-backed ``host`` composition with the work-item read seam replaced by an
in-process :class:`FakeWorkSource` (``bzh:pluggable-seams``) and a
:class:`~blizzard.foundation.clock.FixedClock` the test can advance, so ids order and
timestamps are deterministic (``bzh:injected-clock``). No forge seam is wired: a hub
command node's own forge-facing script (#65/#67) talks HTTP directly, so a test that
reaches a deliver hub node arms :class:`FakeHubCommandRunner` instead.
"""

from __future__ import annotations

import functools
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy import insert as sa_insert

from blizzard.auth_core import Role
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.ids import USER_PREFIX, mint
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.app import create_app
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
from blizzard.hub.domain.graph import Edge, Graph, Node
from blizzard.hub.domain.work import WorkRef
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema
from blizzard.hub.work_sources.annotator import IWorkAnnotator, WorkAnnotateError, WorkStatusMarker
from blizzard.hub.work_sources.closer import IWorkCloser, WorkCloseError, WorkItemGoneError
from blizzard.hub.work_sources.registry import WorkSourceRegistry
from blizzard.hub.work_sources.source import IWorkSource, WorkItem, WorkSourceError

_GRAPH_T0 = datetime(2026, 1, 1, tzinfo=UTC)


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

    ``script`` maps a command string to a queue of :class:`CommandResult`\\ s (popped in
    order, so a command run twice gets its next scripted result, or repeats its last if
    the queue is exhausted); ``calls`` records every ``(command, cwd, env)`` invocation
    for assertion. ``before_run``, when set, is called synchronously inside :meth:`run`
    before returning — a hook for a test that must block there.
    """

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

    Keyed on ``pointer.ref`` (an opaque item token) rather than a URL. A default
    ``title``/``body``/``comments`` answers every pointer; ``by_ref`` overrides the item
    for specific refs, and ``fail_refs`` raises :class:`WorkSourceError` for a ref to
    exercise the per-pointer forge-failure degradation. ``name`` is this fake's
    registered source name — the prefix its ``label`` renders under and the ``source`` a
    pointer it mints carries. ``repo`` is the ``owner/repo`` this fake renders
    ``web_url``s under."""

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

    Rather than couple this repo to ``blizzard-mock`` as a dev dependency (a separate
    uv project), the adapter HTTP shaping is exercised against this minimal
    GitHub-REST-v3 surface — issue read + comments, PR create + merge, plus the
    label routes the annotator seam needs: repo-level label create (422 on
    duplicate), issue-level label add/remove, and a genuinely
    ``Link``-header-paginated, ``labels=`` filtered issue listing.
    ``pull_numbers`` marks which issue numbers the listing renders with a
    ``pull_request`` key. Wrapped in a ``TestClient`` (itself an ``httpx.Client``) so
    the sync adapters drive it directly.
    """
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
        # The narrower `label_add_forbidden` lever fails *only* this route, leaving the
        # repo-label bootstrap and the paired remove-label call working. The broad
        # `forbidden` lever cannot isolate an add failure: it trips the bootstrap POST
        # first, so `set_status` raises before it ever reaches this route.
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
    #: address (``TestClient(hub.app, client=("203.0.113.9", 0))``) — the forwarded-header
    #: trust tests (issue #130) need a concrete IP peer, not the default ``testclient``.
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
) -> HubHarness:
    """A migrated, fully-wired hub over ``tmp_path`` with fake external seams.

    ``work_sources`` is ``{name: FakeWorkSource}``; ``None`` defaults to one source, while
    an explicit ``work_sources={}`` is a legal, deliberately **empty** registry — ``or``
    would silently coerce that back to the default.
    ``hub_command_runner``/``hub_workdir`` are the generic hub command node's mechanism
    seams (#65) — a test binds fakes here; left ``None``, ``build_services`` wires the
    real subprocess/filesystem adapters, rooted under a throwaway tmp dir.
    ``runner_auth_mode`` (issue #86a), ``route_token_mode`` (issue #84b), and
    ``produces_mode`` (issue #113 phase 5) default to ``warn``; a test exercising an
    ``enforce`` rejection path overrides the relevant one. ``auth_mode``/``superuser``
    (issue #91) default to ``none``; a test exercising ``require()`` gating passes
    ``auth_mode="oauth"``. ``oauth_providers`` (issue #92) is
    ``{name: FakeOAuthProvider}`` — the no-network in-repo-fake registry the
    provider-login route tests bind."""
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

    work_source_registry = WorkSourceRegistry(
        work_sources if work_sources is not None else {"default": FakeWorkSource()}
    )
    clock = FixedClock(datetime(2026, 7, 13, tzinfo=UTC))
    events = EventBroker()
    engine = create_engine_from_url(db_url)
    services = build_services(
        engine,
        events=events,
        work_sources=work_source_registry,
        clock=clock,
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
    )
    app = create_app(config, services=services)
    client = TestClient(app)
    # Warm FastAPI's per-router route-resolution cache before any test drives the client.
    # FastAPI (0.139) resolves an included router's routes lazily, caching them by
    # clearing/repopulating an instance list on first use — thread-unsafe if two requests
    # hit the cold cache at once, which the component tier's OS-thread races
    # (test_claim_exactly_once, test_edit_claim_race) can trigger. One throwaway request
    # to a non-matching /api path traverses, and so warms, every API router branch first.
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

    Every upper-tier fixture (``tests/e2e``, ``tests/crash``, ``tests/journey``,
    ``tests/service``) that runs ``blizzard hub init`` and then ingests must declare its
    sources through this, or its own ingests fail. Round-trips through
    :meth:`~blizzard.hub.config.HubConfig.load` -> ``dataclasses.replace`` ->
    :meth:`~blizzard.hub.config.HubConfig.to_toml`; see
    ``tests/crash/support.py::write_runner_config`` for the runner-config counterpart."""
    config = HubConfig.load(hub_dir)
    config = replace(config, work_sources=tuple(sources))
    config.config_path.write_text(config.to_toml())
    return config


def daemon_log_sink(path: Path) -> IO[str]:
    """An append-mode file for a spawned daemon's merged stdout/stderr.

    A long-lived daemon must NEVER be given ``stdout=PIPE`` by a test
    (``bzh:daemon-stdout-to-file``). Nothing in these suites drains those pipes, so the
    daemon runs only until its output fills the ~64 KiB pipe buffer and then blocks in
    ``write`` forever — wedged mid-tick, with every subsequent wait timing out against a
    process that still looks alive. The deadlock is latent in output *volume* rather than
    in anything the test does, so it surfaces as an unrelated-looking assertion far from
    its cause. A file has no such ceiling, and it keeps the daemon's log readable after a
    failure instead of discarded with the pipe.

    One owner for all four daemon-running tiers (issue #145): ``tests/crash``,
    ``tests/service``, ``tests/e2e``, and ``tests/journey`` all spawn through this.
    Short-lived ``subprocess.run(..., capture_output=True)`` calls are unaffected — they
    drain by construction.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("a", buffering=1)


def read_daemon_log(path: Path | None) -> str:
    """A spawned daemon's log text, or a legible stand-in — the early-exit diagnostic.

    Total by design: a daemon that died before its log file was ever created (a bad
    binary path, an immediate ``exec`` failure) must still produce an assertion message
    naming what happened, so a missing/unreadable file degrades to a note rather than
    masking the real failure with an :class:`OSError` of its own.
    """
    if path is None:
        return "<no log file>"
    try:
        return path.read_text()
    except OSError as exc:  # pragma: no cover - defensive, see docstring
        return f"<log {path} unreadable: {exc}>"


@functools.lru_cache(maxsize=1)
def shared_daemon_log_dir() -> Path:
    """The per-process fallback log directory for daemons spawned with no runtime dir.

    The **mock** fleet's daemons (mock hub, mock runner, stub IdP) own no runtime
    directory to put a log beside, and threading one through their ~40 call sites would
    buy nothing a single well-named directory does not. Created once per pytest process
    and reused, so a whole session's mock-daemon logs land together and are still on disk
    after a failure.
    """
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

    Starlette's ``TestClient`` (httpx ``ASGITransport``) buffers a whole response body, so it
    cannot consume an *infinite* live stream incrementally. Instead this drives the
    route's async generator directly with a request that reports itself disconnected, so the
    generator emits its replay tail (newer than ``last_event_id``) and then returns at the
    first liveness check.
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
    """Ingest ``pointers`` (as ``{source, ref}`` dicts) into one chunk and (by default)
    promote it to ready — each dict is converted to its ``{source}:{ref}``
    ingest token before posting.

    Pass ``promote=False`` to assert the not-ready resting state a bare ingest leaves.
    """
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(p) for p in pointers]})
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]
    if promote:
        promoted = hub.client.post(f"/api/chunks/{chunk_id}/promote")
        assert promoted.status_code == 202, promoted.text
    return chunk_id


def write_chunk_pause_facts(tmp_path: Path, chunk_id: str, *facts: tuple[bool, datetime]) -> None:
    """Append ``chunk_pause_facts`` rows for ``chunk_id``, in argument order (issue #46).

    **Not** a stand-in for the pause route (``POST /api/chunks/{id}/pause``, driven for
    real in ``test_chunks_api.py``). This helper exists for the one thing the route cannot
    express: **arbitrary ``set_at`` values** — the route stamps a single ``clock.now()``
    per call, so a fact sequence with *distinct* instants (or a deliberate same-instant
    collision) is unreachable through it.

    Each tuple is ``(paused, set_at)``; write order is the newest-wins order, matching the
    append-only ``id`` the hydration sorts by. The **read** path stays entirely real, so
    nothing asserted through this is a tautology. Opens its own engine on the same
    ``db_url`` :func:`build_hub` derives from ``tmp_path``.
    """
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
        user_id=mint(USER_PREFIX, hub.clock),
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
    ``bzh:utc-instants``): a naive string re-parses fine with ``datetime.fromisoformat``
    on the same box that emitted it, so only the literal trailing designator
    (``+00:00`` / ``Z``) catches the naive-serialization bug.
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


def report_lease(
    hub: HubHarness, chunk_id: str, *, epoch: int, seq: int, runner_id: str = "r1", route_token: str | None = None
) -> dict:
    """Report a runner-minted ``lease.minted`` fact through POST /events.

    A component test that submits a completion calls this first, so the hub knows the
    chunk's latest epoch. ``route_token`` (issue #84b) rides the payload; ``None`` (the
    default) omits it, matching a caller that never claimed under the plaintext.
    """
    payload: dict[str, object] = {"chunk_id": chunk_id, "epoch": epoch}
    if route_token is not None:
        payload["route_token"] = route_token
    resp = hub.client.post(
        "/api/fleet/events",
        json={"runner_id": runner_id, "facts": [{"seq": seq, "kind": "lease.minted", "payload": payload}]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- Migration-test scaffolding --------------------------------------------------
#
# ``graphs``/``chunks`` carry no revision-pinned shape, so these two parent-row seeds are
# identical every time a migration test needs a store at some past revision and can be
# shared. A revision's own frozen table-under-test must NOT move here: a revision pinned
# in time must not import a shape that has since moved on.

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
