"""Shared component-test scaffolding — a fully-wired hub over a tmp sqlite store.

Builds the store-backed ``host`` composition with the work-item read seam replaced by
:class:`FakeWorkSource` (``bzh:pluggable-seams``) and a clock the test can advance
(``bzh:injected-clock``). A deliver hub node's script talks HTTP directly, so arm
:class:`FakeHubCommandRunner`."""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import os
import re
import shutil
import socket
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

import httpx
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy import insert as sa_insert

from blizzard.auth_core import Role
from blizzard.foundation.clock import FixedClock, IClock
from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.ids import USER_PREFIX, Id
from blizzard.foundation.logging import get_logger
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.foundation.trace_spans import FinishedSpan
from blizzard.hub.app import create_app
from blizzard.hub.auth.models import User
from blizzard.hub.auth.oauth.provider import IOAuthProvider
from blizzard.hub.auth.oauth.registry import OAuthProviderRegistry
from blizzard.hub.composition import HubServices, build_hub_core, build_live_config, build_services
from blizzard.hub.config import (
    AUTH_MODE_NONE,
    AUTH_MODE_OAUTH,
    PRODUCES_WARN,
    RESERVED_HUB_SOURCE_NAME,
    ROUTE_TOKEN_WARN,
    RUNNER_AUTH_WARN,
    AuthConfig,
    EgressConfig,
    HubConfig,
)
from blizzard.hub.delivery.command_runner import CommandResult, IHubCommandRunner
from blizzard.hub.delivery.workdir import IHubWorkdir
from blizzard.hub.domain.chunk.delivery_read import DeliveryTrace
from blizzard.hub.domain.chunk.model import (
    Chunk,
    ChunkFacts,
    HubWorkItem,
    IWriteWorkItemRepository,
    WorkItemAuthor,
    WorkRef,
)
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.config.repositories import RepositoryFields
from blizzard.hub.domain.config.secrets import IHubKeyProvider, SecretAlreadyExists, SecretName
from blizzard.hub.domain.execution.fleet import FleetService
from blizzard.hub.domain.graph.model import Edge, Graph, Node
from blizzard.hub.domain.observability.transcripts import TranscriptCaps
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry
from blizzard.hub.egress.writer import (
    EgressBatch,
    EgressFailure,
    EgressPass,
    FilesWritten,
    IEgressWriter,
    ManifestCommitted,
    PlacedFile,
    validate_batch,
)
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.runtime import migration_runner
from blizzard.hub.secrets import hub_key_provider, secret_cipher
from blizzard.hub.store import schema
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.config_apply_store import ConfigApplyStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.internal.secret_store import SecretStore
from blizzard.hub.store.internal.work_source_record_store import WorkSourceRecordStore
from blizzard.hub.system_artifacts import PackagedSystemArtifacts
from blizzard.hub.work_sources.annotator import IWorkAnnotator, WorkAnnotateError, WorkStatusMarker
from blizzard.hub.work_sources.closer import IWorkCloser, WorkCloseError, WorkItemGoneError
from blizzard.hub.work_sources.editor import IWorkEditor
from blizzard.hub.work_sources.internal.hub_work_source import HubWorkSource
from blizzard.hub.work_sources.source import IWorkSource, IWorkSourceRegistry, WorkItem, WorkSourceError
from blizzard.wire.work_source import WorkSourceDocument

_GRAPH_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def hub_store_connections(engine: Engine) -> HubStoreConnections:
    """The ``hub/store/internal/`` seam every adapter test wires over its
    own migrated engine — one helper so every adapter's test file constructs it
    identically."""
    return HubStoreConnections(engine, HubStoreErrorFactory(get_logger("test")))


OP = ChangeContext(actor="op", door=Door.API)


def config_authoring(engine: Engine, *, keys: IHubKeyProvider, clock: IClock) -> ConfigAuthoring:
    """The one configured-record writer over ``engine``, sealing under ``keys``."""
    store = hub_store_connections(engine)
    return ConfigAuthoring(
        work_sources=WorkSourceRecordStore(store),
        repositories=RepositoryRecordStore(store),
        secrets=SecretStore(store),
        cipher=secret_cipher(keys),
        apply_writer=ConfigApplyStore(store),
        clock=clock,
    )


def chunk_stores(engine: Engine, clock: IClock) -> ChunkStores:
    """All chunk-seam adapters over one engine/clock, bundled the same shape
    ``hub/composition.py`` wires in production (:func:`build_chunk_stores`) — the
    store-level test's own single-object fixture-setup convenience a per-seam physical
    split would otherwise take from it. A test calls ``stores.<seam>.<method>(...)`` in
    place of the old single ``ChunkStore``'s bare method call."""
    store = hub_store_connections(engine)
    return build_chunk_stores(store, clock, registry=RunnerRegistryStore(store))


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


class InMemoryTraceExporter:
    """An in-process :class:`ITraceExporter` — records each batch it accepts.

    ``fail`` set makes every export refuse its batch; ``raises`` makes it raise instead.
    ``attempts`` counts every call, accepted or not."""

    def __init__(self) -> None:
        self.batches: list[tuple[FinishedSpan, ...]] = []
        self.attempts = 0
        self.fail = False
        self.raises = False

    def export(self, spans: Sequence[FinishedSpan]) -> bool:
        self.attempts += 1
        if self.raises:
            raise RuntimeError("in-memory trace exporter told to raise")
        if self.fail:
            return False
        self.batches.append(tuple(spans))
        return True

    @property
    def spans(self) -> list[FinishedSpan]:
        return [span for batch in self.batches for span in batch]


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

    def web_url(self, pointer: WorkRef, *, live_holder: str | None) -> str | None:
        return f"http://forge.local/{self.repo}/issues/{pointer.ref}"

    def forge_reference(self, pointer: WorkRef) -> str | None:
        return f"{self.repo}#{pointer.ref}"

    def branch_url(self, repo: str, branch_name: str) -> str | None:
        return f"http://forge.local/{repo}/tree/{branch_name}"


def _conforms_fake_work_source(x: FakeWorkSource) -> IWorkSource:
    return x


class WorkSourceRegistry:
    """A dict-backed work-source registry — the test double for the store-backed one.

    ``annotators``/``closers``/``editors`` are each a subset of ``sources``, so an absent name has no
    write half; ``label_clearers`` holds every forge source's annotator, reached only to clear labels."""

    def __init__(
        self,
        sources: Mapping[str, IWorkSource] | None = None,
        annotators: Mapping[str, IWorkAnnotator] | None = None,
        closers: Mapping[str, IWorkCloser] | None = None,
        editors: Mapping[str, IWorkEditor] | None = None,
        label_clearers: Mapping[str, IWorkAnnotator] | None = None,
    ) -> None:
        self._sources = dict(sources or {})
        self._annotators = dict(annotators or {})
        self._closers = dict(closers or {})
        self._editors = dict(editors or {})
        self._label_clearers = {**dict(label_clearers or {}), **self._annotators}

    def get(self, name: str) -> IWorkSource | None:
        return self._sources.get(name)

    def names(self) -> list[str]:
        return list(self._sources.keys())

    def annotator(self, name: str) -> IWorkAnnotator | None:
        return self._annotators.get(name)

    def annotating_names(self) -> list[str]:
        return list(self._annotators.keys())

    def label_clearer(self, name: str) -> IWorkAnnotator | None:
        return self._label_clearers.get(name)

    def closer(self, name: str) -> IWorkCloser | None:
        return self._closers.get(name)

    def editor(self, name: str) -> IWorkEditor | None:
        return self._editors.get(name)

    def resolve(self, token: str) -> WorkRef | None:
        for source in self._sources.values():
            pointer = source.parse(token)
            if pointer is not None:
                return pointer
        return None


def _conforms_work_source_registry(x: WorkSourceRegistry) -> IWorkSourceRegistry:
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
        self.traces: list[DeliveryTrace | None] = []

    def close(self, pointer: WorkRef, *, trace: DeliveryTrace | None) -> None:
        if pointer.ref in self.gone_refs:
            raise WorkItemGoneError(f"{pointer.ref} no longer exists")
        if pointer.ref in self.fail_refs:
            raise WorkCloseError(f"boom closing {pointer.ref}")
        self.closed.append(pointer)
        self.traces.append(trace)


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
        "issues": issue_store,
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
    def get_comments(
        request: Request, owner: str, repo: str, number: int, page: int = 1, per_page: int = 30
    ) -> JSONResponse:
        key = f"{owner}/{repo}#{number}"
        if key not in issue_store and key not in state.setdefault("issue_state", {}):  # type: ignore[call-overload]
            return JSONResponse(status_code=404, content={"message": "Not Found"})
        comments = issue_store.get(key, {}).get("comments", [])
        chunk = comments[(page - 1) * per_page : page * per_page]
        headers = {}
        if page * per_page < len(comments):
            headers["Link"] = f'<{request.url.include_query_params(page=page + 1, per_page=per_page)}>; rel="next"'
        return JSONResponse(content=[{"body": c} for c in chunk], headers=headers)

    @app.post("/repos/{owner}/{repo}/issues/{number}/comments")
    def post_comment(owner: str, repo: str, number: int, body: dict) -> JSONResponse:
        key = f"{owner}/{repo}#{number}"
        if key not in issue_store:
            return JSONResponse(status_code=404, content={"message": "Not Found"})
        issue_store[key].setdefault("comments", []).append(body["body"])
        return JSONResponse(status_code=201, content={"body": body["body"]})

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
    client.forge_state = state  # type: ignore[attr-defined] # tests flip PR fate (e.g. close-without-merge)
    return client


def forge_state(double: TestClient) -> dict[str, object]:
    """Typed accessor for a :func:`github_double`'s mutable state dict — a test
    seeds/reads ``issue_labels``/``repo_labels``/``pr_numbers``/``forbidden``/
    ``label_add_forbidden`` directly."""
    return double.forge_state  # type: ignore[attr-defined]


class RunnerFleetClient(TestClient):
    """The hub harness's client: before a claim through ``POST /api/fleet/routes`` it registers the
    claiming runner when unregistered, as a live runner does every tick; a standing registration, retired
    included, is left as is. A test pinning the unregistered refusal passes
    ``build_hub(..., auto_register_claimants=False)``."""

    def __init__(self, app: FastAPI, *, fleet: FleetService, registry: IReadRunnerRegistry) -> None:
        super().__init__(app)
        self._fleet = fleet
        self._registry = registry
        self._registering = threading.Lock()

    def post(self, url: Any, *args: Any, **kwargs: Any) -> Any:
        body = kwargs.get("json")
        if str(url) == "/api/fleet/routes" and isinstance(body, dict) and isinstance(body.get("runner_id"), str):
            self._register(body["runner_id"], str(body.get("workspace_id") or "w1"))
        return super().post(url, *args, **kwargs)

    def _register(self, runner_id: str, workspace_id: str) -> None:
        with self._registering:
            if self._registry.get_runner(runner_id) is None:
                self._fleet.register(runner_id, workspace_id)


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
    #: address — the forwarded-header trust tests need a concrete IP peer.
    app: FastAPI | None = None

    def promote(self, chunk_id: str) -> None:
        """Promote ``chunk_id`` through the real ``POST /api/chunks/{id}/promote`` path."""
        assert self.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202


_hub_prototype_lock = threading.Lock()
_hub_prototype_tmp: tempfile.TemporaryDirectory[str] | None = None
_hub_prototype_db: Path | None = None


def hub_migration_prototype() -> Path:
    """A hub store migrated to head exactly once per process; ``build_hub`` copies this
    file rather than re-running all ~89 hub revisions on every call.

    Safe to share across every ``build_hub`` call regardless of the ``HubConfig`` a test
    passes: the migration tree branches on nothing but its own code, never on a config
    field. Lives under its own per-process temp dir, cleaned up by
    :class:`tempfile.TemporaryDirectory`'s own finalizer."""
    global _hub_prototype_tmp, _hub_prototype_db
    with _hub_prototype_lock:
        if _hub_prototype_db is None:
            _hub_prototype_tmp = tempfile.TemporaryDirectory(prefix="blizzard-hub-migration-proto-")
            root = Path(_hub_prototype_tmp.name)
            db_url = f"sqlite:///{root / 'hub.db'}"
            migration_runner(HubConfig(root=root, db_url=db_url)).upgrade("head")
            checkpoint_sqlite(db_url)
            _hub_prototype_db = root / "hub.db"
        return _hub_prototype_db


def checkpoint_sqlite(db_url: str) -> None:
    """Flush a sqlite file's WAL back into itself and drop the connection — a bare copy
    of the ``.db`` file is only a complete store once no ``-wal``/``-shm`` sidecar is
    load-bearing."""
    engine = create_engine_from_url(db_url)
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        engine.dispose()


#: The repository every hub built without an explicit ``repositories`` carries — the ``acme/widget``
#: the component tier's commits name — so a deliver node resolves them.
DEFAULT_FIXTURE_REPOSITORIES = (
    RepositoryFields(
        forge_api_url="http://forge.fixture",
        owner="acme",
        repo="widget",
        base_branch="main",
        secret_name="fixture-forge-token",
    ),
)


def seed_repositories(engine: Engine, repositories: Sequence[RepositoryFields], *, tmp_path: Path) -> None:
    """Write ``repositories`` and the secret they name straight into the store, as an operator's
    ``blizzard hub repo create`` would — a no-op for none, and for a store an earlier build already seeded."""
    if not repositories:
        return
    authoring = config_authoring(
        engine, keys=hub_key_provider({}, data_dir=tmp_path), clock=FixedClock(datetime(2026, 7, 12, tzinfo=UTC))
    )
    for fields in repositories:
        name = f"{fields.owner}-{fields.repo}"
        if RepositoryRecordStore(hub_store_connections(engine)).get(name) is not None:
            continue
        with contextlib.suppress(SecretAlreadyExists):
            authoring.create_secret(SecretName.parse(fields.secret_name), "fixture-token", OP)
        authoring.create_repository(name, fields, OP)


def build_hub(
    tmp_path: Path,
    *,
    work_sources: dict[str, FakeWorkSource] | None = None,
    hub_command_runner: IHubCommandRunner | None = None,
    hub_workdir: IHubWorkdir | None = None,
    repositories: Sequence[RepositoryFields] | None = None,
    public_url: str | None = None,
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
    trace_exporter: ITraceExporter | None = None,
    tracing: TracingConfig | None = None,
    tracing_settings: TracingSettings | None = None,
    egress: EgressConfig | None = None,
    egress_path_key: bytes | None = None,
    auto_register_claimants: bool = True,
) -> HubHarness:
    """A migrated, fully-wired hub over ``tmp_path`` with fake external seams.

    ``auto_register_claimants`` (on by default) makes ``hub.client`` a :class:`RunnerFleetClient`.
    ``work_sources=None`` defaults to one fake source, while ``{}`` is a deliberately **empty** registry;
    ``hub_command_runner``/``hub_workdir`` left ``None`` wire real adapters."""
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
        tracing=tracing or TracingConfig(),
        egress=egress or EgressConfig(),
    )
    # A second build over the same ``tmp_path`` reopens the store the first one wrote;
    # copying over it would discard that state, so only a fresh directory takes the copy.
    db_path = tmp_path / "hub.db"
    if db_path.exists():
        migration_runner(config).upgrade("head")
    else:
        shutil.copyfile(hub_migration_prototype(), db_path)
    engine = create_engine_from_url(db_url)
    seed_repositories(engine, DEFAULT_FIXTURE_REPOSITORIES if repositories is None else repositories, tmp_path=tmp_path)

    built_sources: dict[str, IWorkSource] = dict(
        work_sources if work_sources is not None else {"default": FakeWorkSource()}
    )
    clock = FixedClock(datetime(2026, 7, 13, tzinfo=UTC))
    editors: dict[str, IWorkEditor] = {}
    # The built-in `hub` source is seated as a source, an editor and a closer unconditionally,
    # mirroring `WorkSourceEntry.registry`'s production wiring.
    closers: dict[str, IWorkCloser] = {}
    core = build_hub_core(engine, clock=clock)
    hub_source = HubWorkSource(core.work_item_store, core.work_item_edits, core.users, core.garden_proposal_resolution)
    built_sources[RESERVED_HUB_SOURCE_NAME] = hub_source
    editors[RESERVED_HUB_SOURCE_NAME] = hub_source
    closers[RESERVED_HUB_SOURCE_NAME] = hub_source
    work_source_registry = WorkSourceRegistry(built_sources, closers=closers, editors=editors)
    events = EventBroker()
    secret_keys = hub_key_provider({}, data_dir=tmp_path)
    live = build_live_config(core, secret_keys=secret_keys)
    services = build_services(
        core,
        events=events,
        work_sources=work_source_registry,
        secrets=live.secrets,
        hub_command_runner=hub_command_runner,
        hub_workdir=hub_workdir,
        hub_workdir_root=tmp_path / "hub_workdirs",
        hub_marker_callback_base_url="http://testserver",
        public_url=public_url,
        oauth_registry=OAuthProviderRegistry(oauth_providers) if oauth_providers is not None else None,
        # The IdP signing-key lifecycle — wired only under `oauth`, mirroring
        # `hub/app.py`'s own `build_hosted_app` gating exactly.
        signing_keys_dir=(tmp_path / "auth" / "signing-keys") if auth_mode == AUTH_MODE_OAUTH else None,
        secret_keys=secret_keys,
        trusted_proxies=TrustedProxies.parse(config.trusted_proxies),
        transcript_caps=transcript_caps,
        system_artifacts=system_artifacts,
        trace_exporter=trace_exporter,
        tracing_settings=tracing_settings,
        tracing=config.tracing,
        egress=config.egress,
        egress_path_key=egress_path_key,
    )
    app = create_app(config, services=services)
    client = (
        RunnerFleetClient(app, fleet=services.fleet, registry=services.registry)
        if auto_register_claimants
        else TestClient(app)
    )
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


#: The one secret every records-seeded fixture source names — no fixture forge checks a token.
FIXTURE_FORGE_SECRET = "fixture-forge-token"


def fixture_work_source(name: str, locator: str, api_base: str, *, annotate: bool = False) -> WorkSourceDocument:
    """A GitHub-shaped source on a fixture forge, naming :data:`FIXTURE_FORGE_SECRET`."""
    return WorkSourceDocument(
        name=name, provider="github", locator=locator, api_base=api_base, annotate=annotate, secret=FIXTURE_FORGE_SECRET
    )


def ensure_fixture_secret(hub: httpx.Client) -> None:
    """Create :data:`FIXTURE_FORGE_SECRET` unless an earlier seeding already did."""
    secret = hub.post("/api/secrets", json={"name": FIXTURE_FORGE_SECRET, "value": "fixture-token"})
    assert secret.status_code in (201, 409), secret.text


def fixture_repository(name: str, forge_api_url: str, *, owner: str, base_branch: str = "main") -> dict[str, str]:
    """A repository record body on a fixture forge, naming :data:`FIXTURE_FORGE_SECRET`."""
    return {
        "name": name,
        "forge_api_url": forge_api_url,
        "owner": owner,
        "repo": name,
        "base_branch": base_branch,
        "secret_name": FIXTURE_FORGE_SECRET,
    }


def fixture_repositories(sources: Sequence[WorkSourceDocument], forge_api_url: str) -> list[dict[str, str]]:
    """One repository record per source's ``owner/repo`` locator, on the fixture forge's ``main``."""
    return [
        fixture_repository(source.locator.rpartition("/")[2], forge_api_url, owner=source.locator.partition("/")[0])
        for source in sources
    ]


def create_repositories(hub: httpx.Client, repositories: Sequence[dict[str, str]]) -> None:
    """Create repository ``repositories`` — and the secret they name — through a running hub's own
    API, so a deliver node resolves its chunk's commits against them."""
    ensure_fixture_secret(hub)
    for repository in repositories:
        created = hub.post("/api/repositories", json=repository)
        assert created.status_code == 201, created.text


def create_work_sources(hub: httpx.Client, sources: Sequence[WorkSourceDocument]) -> None:
    """Create ``sources``, and the secret they name, through a running hub's own API.

    Every upper-tier fixture that stands a real hub up and then ingests seeds its sources
    through this once the hub answers, or its own ingests fail."""
    if not sources:
        return
    ensure_fixture_secret(hub)
    for source in sources:
        created = hub.post("/api/work-sources", json=source.model_dump())
        assert created.status_code == 201, created.text


def write_mock_harness_credentials(runner_dir: Path) -> tuple[str, str]:
    """Fixture-controlled, always-valid credential files for the harness-health probes
    — neither mock CLI is a real, logged-in provider, so a runner driven
    against them needs its own disposable stand-ins rather than reading whatever (if
    anything) sits at each probe's real-credential-store default on this machine.
    Returns ``(claude_code_credentials_path, opencode_auth_path)``."""
    claude_credentials = runner_dir / "mock-claude-credentials.json"
    claude_credentials.write_text(json.dumps({"claudeAiOauth": {"accessToken": "mock-token"}}))
    opencode_auth = runner_dir / "mock-opencode-auth.json"
    opencode_auth.write_text(json.dumps({"anthropic": {"type": "oauth"}}))
    return str(claude_credentials), str(opencode_auth)


def daemon_log_sink(path: Path) -> IO[str]:
    """An append-mode file for a spawned daemon's merged stdout/stderr.

    A long-lived daemon must NEVER get ``stdout=PIPE`` (``bzh:daemon-stdout-to-file``):
    nothing here drains it, so the daemon wedges once the pipe buffer fills, surfacing
    as an unrelated timeout far from the cause."""
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


_PORT_FLOOR = 20000
_PORT_CEILING = 32768  # the kernel's ephemeral range starts here
_PORT_BAND = 256
_port_lock = threading.Lock()
_port_cursor: list[int] = []


def _worker_port_band() -> int:
    """This worker's first port: bands are disjoint within a run and shifted per run."""
    run_id = os.environ.get("PYTEST_XDIST_TESTRUNUID") or f"{os.getpid()}-{time.time_ns()}"
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    bands = (_PORT_CEILING - _PORT_FLOOR) // _PORT_BAND
    run_offset = int.from_bytes(hashlib.sha256(run_id.encode()).digest()[:4], "big")
    return _PORT_FLOOR + ((run_offset + int(worker.removeprefix("gw"))) % bands) * _PORT_BAND


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def free_port() -> int:
    """A localhost port no other xdist worker of this run can be handed before its daemon binds."""
    with _port_lock:
        if not _port_cursor:
            _port_cursor.append(_worker_port_band())
        band = _port_cursor[0] - (_port_cursor[0] - _PORT_FLOOR) % _PORT_BAND
        for _ in range(_PORT_BAND):
            port = _port_cursor[0]
            _port_cursor[0] = band + (port - band + 1) % _PORT_BAND
            if _port_is_free(port):
                return port
    raise RuntimeError(f"no free port in the band starting at {band}")


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


def chunk_facts_of(hub: HubHarness, chunk_id: str) -> ChunkFacts:
    """A chunk's current facts, or the unminted default — the same fallback every route
    applies, for a test calling a write-verb domain service directly rather than through
    its route (which would otherwise supply this from its own ``ChunkChanged.before``)."""
    return ChunkFacts.or_default(hub.services.chunks.facts.load_facts(chunk_id))


def write_chunk_pause_facts(tmp_path: Path, chunk_id: str, *facts: tuple[bool, datetime]) -> None:
    """Append ``chunk_pause_facts`` rows for ``chunk_id``, in argument order.

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

    No login mechanism exists yet, so a test wanting a ``ResolvedIdentity``
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

    Pins the wire **bytes**, not a parsed-then-compared value
    (``bzh:utc-instants``): a naive string re-parses fine on the same box that emitted it,
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


def seed_chunk_record(stores: ChunkStores, chunk: Chunk) -> None:
    """Insert ``chunk``'s rows the way ingest does — under its pointers' locks — for a fixture
    that needs a chunk without going through an ingest's guard."""
    with stores.exclusive.locked_work_refs(chunk.work_refs) as handle:
        stores.record.mint_locked(handle, chunk)


def make_ready(hub: HubHarness, chunk_id: str) -> None:
    """Promote ``chunk_id`` through the promote service — a replay once promoted — so a claim
    finds it ``ready``. Bypasses the HTTP route: no operator session and no SSE frame."""
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None, f"unknown chunk {chunk_id}"
    hub.services.promote.promote(chunk, statuses=hub.services.chunks.facts.load_live_statuses())


def claim_route(hub: HubHarness, chunk_id: str, *, runner_id: str = "r1") -> dict:
    """Promote ``chunk_id`` (a replay once promoted) and claim it for ``runner_id`` through POST
    /routes — the route a runner must hold before its first ``lease.minted`` above the claim's
    reservation is admitted. Only a ``ready`` chunk is claimable."""
    make_ready(hub, chunk_id)
    resp = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": runner_id, "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def report_lease(
    hub: HubHarness, chunk_id: str, *, epoch: int, seq: int, runner_id: str = "r1", route_token: str | None = None
) -> dict:
    """Report a runner-minted ``lease.minted`` fact through POST /events.

    A component test calls this first so the hub knows the chunk's latest epoch.
    ``route_token`` rides the payload; ``None`` omits it, matching a caller
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


def report_escalation(
    hub: HubHarness,
    chunk_id: str,
    *,
    epoch: int,
    seq: int,
    runner_id: str = "r1",
    takeover_command: str = "cd env && claude --resume s",
    **fields: object,
) -> dict:
    """Report a runner's ``escalation.recorded`` fact through POST /events — the chunk derives
    ``needs_human`` when the fence admits it. ``fields`` rides the payload verbatim
    (``lease_id``, ``wrapped_takeover_command``, ``cause``, ``detail``, ``route_token``); the
    caller reads ``applied``/``rejected`` off the returned ack."""
    payload: dict[str, object] = {"chunk_id": chunk_id, "epoch": epoch, "takeover_command": takeover_command, **fields}
    resp = hub.client.post(
        "/api/fleet/events",
        json={"runner_id": runner_id, "facts": [{"seq": seq, "kind": "escalation.recorded", "payload": payload}]},
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


def mint_graph(engine: Engine, graph_id: str, *, name: str, nodes: dict[str, str], at: datetime) -> None:
    """Mint one graph with real, named nodes through the graph store's own write path —
    ``nodes`` maps node id to node name, the first being the entry. ``seed_graph`` writes no
    ``graph_nodes``, so a test that needs names to resolve mints through here."""
    node_rows = [
        Node(
            node_id=node_id,
            graph_id=graph_id,
            name=node_name,
            executor=Executor.RUNNER,
            prompt="p",
            checks=[],
            produces=[],
            session=SessionMode.FRESH,
            judged_by=JudgedBy.WORKER,
            retries_max=None,
            retries_exhausted=None,
        )
        for node_id, node_name in nodes.items()
    ]
    graph = Graph(
        graph_id=graph_id, name=name, entry_node_id=node_rows[0].node_id, nodes=node_rows, edges=[], created_at=at
    )
    GraphStore(hub_store_connections(engine)).mint(graph, definition_yaml="", at=at)


def seed_chunk(conn: sa.Connection, chunk_id: str, *, graph_id: str, at: datetime) -> None:
    """Seed one ``chunks`` parent row — the FK a seeded route/pointer/etc. needs."""
    conn.execute(sa.insert(_CHUNKS).values(chunk_id=chunk_id, graph_id=graph_id, minted_at=at))


def seed_lease(engine: Engine, chunk_id: str, *, epoch: int, runner_id: str, at: datetime) -> None:
    """Seed one hub ``lease_facts`` row with its epoch's owner — a runner's, or the hub's for
    ``runner_id`` ``hub`` — the shape an admitted mint leaves, for a test that only needs the
    chunk's fence at ``epoch``. Head-schema only."""
    with engine.begin() as conn:
        conn.execute(
            sa.insert(schema.lease_facts).values(chunk_id=chunk_id, epoch=epoch, runner_id=runner_id, minted_at=at)
        )
        conn.execute(
            sa.insert(schema.epoch_owners).values(
                chunk_id=chunk_id, epoch=epoch, runner_id=None if runner_id == "hub" else runner_id, recorded_at=at
            )
        )


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
) -> HubWorkItem:
    """Seed one hub-owned work item plus its resting chunk, mirroring production's own
    two-step mint (``WorkItemEditService.create``) — there is no chunkless
    filing path to seed around. Callers still seed ``graph_id``'s own row first
    (``seed_graph``); this only seeds the item and its chunk."""
    ref = store.allocate_ref(source)
    pointer = WorkRef(source=source, ref=ref)
    chunk = Chunk(chunk_id=f"ch_{ref}", graph_id=graph_id, work_refs=[pointer], minted_at=at)
    return store.create_with_chunk(
        pointer=pointer, title=title, body=body, author=author, stated_priority=stated_priority, at=at, chunk=chunk
    )


@contextmanager
def capture_statements(engine: Engine) -> Iterator[list[tuple[str, Any]]]:
    """Every statement ``engine`` issues while the context is open, in issue order —
    statement text plus its bound parameters, ready to re-run later (e.g. under
    ``EXPLAIN QUERY PLAN``) with no drift from what was actually sent."""
    statements: list[tuple[str, Any]] = []

    def _listener(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _listener)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _listener)


def count_queries(engine: Engine, fn: Callable[[], object]) -> int:
    """How many statements ``fn`` issues on ``engine`` — what a bulk-read test asserts is
    flat as the fleet grows, rather than growing per chunk."""
    with capture_statements(engine) as statements:
        fn()
    return len(statements)


def count_rows_read(engine: Engine, fn: Callable[[], object]) -> int:
    """How many rows ``fn``'s ``SELECT`` statements return on ``engine`` — the row-volume
    counterpart of :func:`count_queries`. A statement count stays flat while a read still
    fetches (and derives) a row per chunk ever minted; this is what a live-set read asserts
    is flat as only the terminal-chunk count grows (``bzh:live-set-read``). Each captured
    ``SELECT`` is re-counted on a separate connection with its exact parameters."""
    with capture_statements(engine) as statements:
        fn()
    total = 0
    with engine.connect() as conn:
        for statement, parameters in statements:
            if statement.lstrip().upper().startswith("SELECT"):
                total += conn.exec_driver_sql(f"SELECT count(*) FROM ({statement})", parameters).scalar_one()
    return total


def explain_query_plan(engine: Engine, statement: str, parameters: Any) -> Sequence[sa.Row[Any]]:
    """Runs ``EXPLAIN QUERY PLAN`` for one captured statement, re-executed with its exact
    captured parameters."""
    with engine.connect() as conn:
        return conn.exec_driver_sql(f"EXPLAIN QUERY PLAN {statement}", parameters).all()


_SCAN_OR_SEARCH = re.compile(r"^(SCAN|SEARCH) (\S+)(.*)$")
_ALIASED_FROM_ITEM = re.compile(r"\b(?:FROM|JOIN)\s+(\w+)\s+AS\s+(\w+)", re.IGNORECASE)


def _statement_aliases(statement: str) -> dict[str, str]:
    """Maps every ``<table> AS <alias>`` from item in ``statement`` back to its real table —
    a plan row names the alias sqlite was given, not the table it reads (e.g. ``leases``
    joined to itself as ``later_escalation_leases`` for a self-correlated NOT EXISTS)."""
    return {alias: table for table, alias in _ALIASED_FROM_ITEM.findall(statement)}


def _offending_table(detail: str, tables: set[str], aliases: dict[str, str]) -> str | None:
    """``detail`` is a plan row's last column, e.g. ``SCAN t``, ``SEARCH t USING INDEX ix (...)``, or ``SEARCH t USING
    AUTOMATIC COVERING INDEX (...)``; ``t`` may itself be an alias, resolved back to its real table via ``aliases``.
    Offends when the resolved table names one of ``tables`` and is either a bare scan with no ``USING [COVERING]
    INDEX`` clause, or any search/scan through sqlite's own automatic index — a real named index is never an
    offense."""
    match = _SCAN_OR_SEARCH.match(detail)
    if match is None:
        return None
    verb, name, rest = match.groups()
    table = aliases.get(name, name)
    if table not in tables:
        return None
    if "AUTOMATIC" in rest:
        return table
    if verb == "SCAN" and "USING" not in rest:
        return table
    return None


def offending_index_scans(
    engine: Engine, statements: Iterable[tuple[str, Any]], tables: Iterable[str]
) -> list[tuple[str, sa.Row[Any]]]:
    """Re-runs every distinct captured ``SELECT`` in ``statements`` under ``EXPLAIN QUERY
    PLAN`` and returns ``(table, plan row)`` for each row that scans or automatic-indexes
    into one of ``tables`` instead of a real named index — the shared classifier a
    component-tier gate drives per store against its own table vocabulary."""
    vocabulary = set(tables)
    offenders: list[tuple[str, sa.Row[Any]]] = []
    seen: set[str] = set()
    for statement, parameters in statements:
        if not statement.lstrip().upper().startswith("SELECT"):
            continue
        if statement in seen:
            continue
        seen.add(statement)
        aliases = _statement_aliases(statement)
        for row in explain_query_plan(engine, statement, parameters):
            table = _offending_table(row.detail, vocabulary, aliases)
            if table is not None:
                offenders.append((table, row))
    return offenders


class InMemoryEgressWriter:
    """An in-process :class:`IEgressWriter` — holds placed batches and manifests, runs the shared row validation.

    ``fail`` set makes every write and commit return that failure."""

    def __init__(self) -> None:
        self.batches: list[EgressBatch] = []
        self.manifests: list[tuple[EgressPass, tuple[PlacedFile, ...]]] = []
        self.fail: EgressFailure | None = None

    def write(self, batch: EgressBatch) -> FilesWritten | EgressFailure:
        if self.fail is not None:
            return self.fail
        if (invalid := validate_batch(batch)) is not None:
            return invalid
        self.batches.append(batch)
        if not batch.rows:
            return FilesWritten(())
        schema = batch.schema
        return FilesWritten(
            (
                PlacedFile(
                    path=f"{schema.name}/v{schema.major_version}/date={batch.partition}/{len(self.batches)}",
                    dataset=schema.name,
                    version=schema.major_version,
                    partition=batch.partition,
                    rows=len(batch.rows),
                    first_position=batch.rows[0].position,
                    last_position=batch.rows[-1].position,
                    sha256="0" * 64,
                ),
            )
        )

    def commit_pass(self, egress_pass: EgressPass, placed: Sequence[PlacedFile]) -> ManifestCommitted | EgressFailure:
        if self.fail is not None:
            return self.fail
        self.manifests.append((egress_pass, tuple(placed)))
        return ManifestCommitted(f"_manifests/{len(self.manifests)}.json")


def _conforms_in_memory_egress_writer(x: InMemoryEgressWriter) -> IEgressWriter:
    return x
