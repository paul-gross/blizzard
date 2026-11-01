"""The GitHub-shaped work-source binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.hub.work_sources.source.IWorkSource` against a GitHub REST
v3 surface. Confined to ``internal/`` (``bzh:dependency-inversion``); ``httpx2`` is used
only here. One instance per work-source record revision, pinned to its own ``repo``
and ``web_base`` and carrying its own credentialed client."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

import httpx2

from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import adapter_model
from blizzard.foundation.untyped import as_list, as_table
from blizzard.hub.domain.chunk.delivery_read import DeliveryTrace
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.work_sources.annotator import IWorkAnnotator, WorkAnnotateError, WorkStatusMarker
from blizzard.hub.work_sources.closer import IWorkCloser, WorkCloseError, WorkItemGoneError
from blizzard.hub.work_sources.rate_gate import IWorkSourceRateGate
from blizzard.hub.work_sources.source import IWorkSource, IWorkSourceBatchReader, WorkItem, WorkSourceError

_log = get_logger("blizzard.hub.work_sources")

# A GitHub-shaped issue reference — {owner}/{repo}/issues/{number}, with or without a
# leading scheme://host and the REST /repos/ prefix.
_ISSUE_URL_RE = re.compile(r"(?:^|/)(?:repos/)?(?P<owner>[^/:#]+)/(?P<repo>[^/:#]+)/issues/(?P<number>\d+)/?$")


@adapter_model
@dataclass(frozen=True)
class Label:
    """One domain marker rendered as the GitHub label this adapter writes."""

    marker: WorkStatusMarker

    #: Blizzard cyan, hex-without-hash as GitHub's label API spells colors.
    COLORS: ClassVar[dict[WorkStatusMarker, str]] = {
        WorkStatusMarker.INGESTED: "5cd1e5",
        WorkStatusMarker.IN_PROGRESS: "2b6675",
    }

    @property
    def name(self) -> str:
        """``blizzard:ingested`` / ``blizzard:in-progress`` — the wire form dashes what
        the domain enum spells with an underscore."""
        return f"blizzard:{self.marker.value.replace('_', '-')}"

    @property
    def color(self) -> str:
        return self.COLORS[self.marker]

    @property
    def excluded(self) -> Label:  # ast-grep-ignore: bzh:property-delegates
        other = WorkStatusMarker.IN_PROGRESS if self.marker is WorkStatusMarker.INGESTED else WorkStatusMarker.INGESTED
        return Label(other)


class GitHubWorkSource:
    """Vendor-native issue reader over a GitHub-shaped forge, pinned to one repo.

    Also implements :class:`~blizzard.hub.work_sources.annotator.IWorkAnnotator` and
    :class:`~blizzard.hub.work_sources.closer.IWorkCloser` over the same client."""

    def __init__(
        self,
        client: httpx2.Client,
        *,
        source_id: str,
        slug: str,
        repo: str,
        web_base: str,
        gate: IWorkSourceRateGate | None = None,
    ) -> None:
        """``source_id`` is the key every pointer it parses holds; ``slug`` is the source's current slug, which its
        labels and error text render — the adapter is rebuilt when the record's revision moves. ``gate`` admits each
        forge-calling method once before its first request; ``None`` admits every call."""
        self._client = client
        self._gate = gate
        self._id = source_id
        self._name = slug
        self._repo = repo
        self._web_base = web_base.rstrip("/")
        self._labels_bootstrapped = False

    @property
    def client(self) -> httpx2.Client:
        return self._client

    @property
    def web_base(self) -> str:
        return self._web_base

    def parse(self, token: str) -> WorkRef | None:
        """This source's own ingest-token forms into a pointer, or ``None`` when ``token``
        isn't shaped for it: ``{id}:{number}``, ``{id}#{number}``, or an issue URL
        (full or schemeless) naming *this binding's own configured* ``repo``. A slug prefix is not
        matched."""
        for sep_char in (":", "#"):
            prefix, sep, ref = token.partition(sep_char)
            if sep and prefix == self._id and ref.isdigit():
                return WorkRef(source=self._id, ref=ref)
        match = _ISSUE_URL_RE.search(token)
        if match is not None and f"{match['owner']}/{match['repo']}" == self._repo:
            return WorkRef(source=self._id, ref=match["number"])
        return None

    def _turned_away(self) -> datetime | None:
        return self._gate.admit() if self._gate is not None else None

    def _rate_refusal(self, exc: httpx2.HTTPError) -> datetime | None:
        """When ``exc`` is the forge refusing for rate, the instant it lifts — the gate marking the budget spent."""
        if self._gate is None or not isinstance(exc, httpx2.HTTPStatusError):
            return None
        response = exc.response
        return self._gate.refused(response.status_code, response.headers, _message(response))

    def fetch(self, pointer: WorkRef) -> WorkItem:
        if (resume_at := self._turned_away()) is not None:
            raise WorkSourceError(
                f"{self._name}#{pointer.ref}: rate budget low until {resume_at}", retry_after=resume_at
            )
        base = f"/repos/{self._repo}/issues/{pointer.ref}"
        try:
            issue = self._client.get(base)
            issue.raise_for_status()
            comments = self._client.get(f"{base}/comments")
            comments.raise_for_status()
        except httpx2.HTTPError as exc:
            _log.error("work-item fetch failed", source=pointer.source, ref=pointer.ref, error=str(exc))
            raise WorkSourceError(
                f"failed to read {self._name}#{pointer.ref}: {exc}", retry_after=self._rate_refusal(exc)
            ) from exc
        return WorkItem(
            body=str(issue.json().get("body") or ""),
            title=str(issue.json().get("title") or ""),
            comments=[str(c.get("body") or "") for c in comments.json()],
        )

    def fetch_many(
        self, pointers: Sequence[WorkRef], *, include_comments: bool
    ) -> Mapping[WorkRef, WorkItem | WorkSourceError]:
        """:meth:`fetch` over every pointer in one GraphQL request, admitted through the gate once. A turn-away or a
        rate refusal of any form (a ``403``/``429``, or a ``200`` whose ``errors`` carry ``RATE_LIMITED``) errors
        every pointer with the instant the budget lifts; any other transport or HTTP failure errors every pointer
        alike; a pointer the answer holds no item for, or whose ref is no issue number, errors alone."""
        numbered = [p for p in pointers if p.ref.isdigit()]
        result: dict[WorkRef, WorkItem | WorkSourceError] = {
            p: WorkSourceError(f"failed to read {self._name}#{p.ref}: not an issue number")
            for p in pointers
            if not p.ref.isdigit()
        }
        result.update(self._fetch_numbered(numbered, include_comments))
        return result

    def _fetch_numbered(
        self, pointers: Sequence[WorkRef], include_comments: bool
    ) -> Mapping[WorkRef, WorkItem | WorkSourceError]:
        if not pointers:
            return {}
        if (resume_at := self._turned_away()) is not None:
            return {
                p: WorkSourceError(f"{self._name}#{p.ref}: rate budget low until {resume_at}", retry_after=resume_at)
                for p in pointers
            }
        try:
            answer = self._client.post(
                self._graphql_url(),
                json={"query": _batch_query(pointers, include_comments), "variables": self._owner_name()},
            )
            answer.raise_for_status()
            payload = as_table(answer.json()) or {}
        except (httpx2.HTTPError, ValueError) as exc:
            _log.error("work-item batch fetch failed", source=self._name, count=len(pointers), error=str(exc))
            resume = self._rate_refusal(exc) if isinstance(exc, httpx2.HTTPError) else None
            return {
                p: WorkSourceError(f"failed to read {self._name}#{p.ref}: {exc}", retry_after=resume) for p in pointers
            }
        errors = as_list(payload.get("errors")) or []
        if (resume_at := self._graphql_rate_refusal(errors, answer.headers)) is not None:
            return {
                p: WorkSourceError(f"{self._name}#{p.ref}: rate budget low until {resume_at}", retry_after=resume_at)
                for p in pointers
            }
        data = as_table(payload.get("data")) or {}
        nodes = as_table(data.get("repository")) or {}
        return {p: _batch_item(self._name, p, nodes.get(f"i{p.ref}"), errors) for p in pointers}

    def _owner_name(self) -> dict[str, str]:
        owner, _, name = self._repo.partition("/")
        return {"owner": owner, "name": name}

    def _graphql_url(self) -> str:
        """The forge's GraphQL address from its REST base: GHES serves it at ``/api/graphql`` beside ``/api/v3``,
        every other base (``https://api.github.com``, the mock forge) at ``/graphql``."""
        base = str(self._client.base_url).rstrip("/")
        return f"{base.removesuffix('/api/v3')}/api/graphql" if base.endswith("/api/v3") else f"{base}/graphql"

    def _graphql_rate_refusal(self, errors: list[object], headers: Mapping[str, str]) -> datetime | None:
        """When a ``200`` answer's ``errors`` carry ``RATE_LIMITED``, the instant the budget lifts — the gate marking
        it spent as it does a ``429``."""
        if self._gate is None or not any((as_table(e) or {}).get("type") == "RATE_LIMITED" for e in errors):
            return None
        return self._gate.refused(429, {**headers, "x-ratelimit-remaining": "0"}, "RATE_LIMITED")

    def label(self, pointer: WorkRef) -> str | None:
        """``{slug}#{ref}`` — always renders; ``ref`` is opaque here."""
        return f"{self._name}#{pointer.ref}"

    def web_url(self, pointer: WorkRef, *, live_holder: str | None) -> str | None:
        """This source's address never depends on which chunk holds the pointer, so
        ``live_holder`` is ignored."""
        return f"{self._web_base}/{self._repo}/issues/{pointer.ref}"

    def forge_reference(self, pointer: WorkRef) -> str | None:
        """``{owner}/{repo}#{ref}`` from the configured ``repo``, not the slug."""
        return f"{self._repo}#{pointer.ref}"

    # -- IWorkAnnotator ------------------------------------------------------

    def _ensure_labels_bootstrapped(self) -> None:
        """Create both marker labels on this repo before the first write of the
        process, tolerating GitHub's 422 for an already-existing label. Cached
        per instance — every subsequent write skips straight to the issue call."""
        if self._labels_bootstrapped:
            return
        for marker in WorkStatusMarker:
            label = Label(marker)
            try:
                resp = self._client.post(f"/repos/{self._repo}/labels", json={"name": label.name, "color": label.color})
                if resp.status_code != 422:
                    resp.raise_for_status()
            except httpx2.HTTPError as exc:
                _log.error("label bootstrap failed", source=self._name, label=label.name, error=str(exc))
                raise WorkAnnotateError(
                    f"failed to bootstrap label {label.name!r} for {self._name}: {exc}",
                    retry_after=self._rate_refusal(exc),
                ) from exc
        self._labels_bootstrapped = True

    def set_status(self, pointer: WorkRef, marker: WorkStatusMarker) -> None:
        """Add ``marker``'s label and delete the other one's, a 404 on the delete tolerated."""
        if (resume_at := self._turned_away()) is not None:
            raise WorkAnnotateError(f"{self._name}: rate budget low until {resume_at}", retry_after=resume_at)
        self._ensure_labels_bootstrapped()
        try:
            added = self._client.post(f"/repos/{self._repo}/issues/{pointer.ref}/labels", json=[Label(marker).name])
            added.raise_for_status()
            other = Label(marker).excluded.name
            resp = self._client.delete(f"/repos/{self._repo}/issues/{pointer.ref}/labels/{other}")
            if resp.status_code != 404:
                resp.raise_for_status()
        except httpx2.HTTPError as exc:
            _log.error("set-status failed", source=self._name, ref=pointer.ref, marker=marker.value, error=str(exc))
            raise WorkAnnotateError(
                f"failed to set {marker.value} on {self._name}#{pointer.ref}: {exc}",
                retry_after=self._rate_refusal(exc),
            ) from exc

    def clear_status(self, pointer: WorkRef) -> None:
        """Remove every marker label from ``pointer`` — a 404 (label already
        absent) is the expected steady state, not a failure."""
        if (resume_at := self._turned_away()) is not None:
            raise WorkAnnotateError(f"{self._name}: rate budget low until {resume_at}", retry_after=resume_at)
        self._ensure_labels_bootstrapped()
        try:
            for marker in WorkStatusMarker:
                resp = self._client.delete(f"/repos/{self._repo}/issues/{pointer.ref}/labels/{Label(marker).name}")
                if resp.status_code != 404:
                    resp.raise_for_status()
        except httpx2.HTTPError as exc:
            _log.error("clear-status failed", source=self._name, ref=pointer.ref, error=str(exc))
            raise WorkAnnotateError(
                f"failed to clear status on {self._name}#{pointer.ref}: {exc}", retry_after=self._rate_refusal(exc)
            ) from exc

    def marked_refs(self) -> dict[WorkRef, frozenset[WorkStatusMarker]]:
        """Every ref this repo currently labels, discovered per marker via a
        paginated, ``state=all`` issue listing — closed issues can still carry a
        stale label, and GitHub's default page size would silently strand labels
        past the first page. Pull-request entries (GitHub's issue-list endpoint
        returns both) are filtered out."""
        if (resume_at := self._turned_away()) is not None:
            raise WorkAnnotateError(f"{self._name}: rate budget low until {resume_at}", retry_after=resume_at)
        found: dict[WorkRef, set[WorkStatusMarker]] = {}
        try:
            for marker in WorkStatusMarker:
                url: str | None = f"/repos/{self._repo}/issues"
                params: dict[str, str | int] | None = {
                    "labels": Label(marker).name,
                    "state": "all",
                    "per_page": 100,
                }
                while url is not None:
                    resp = self._client.get(url, params=params)
                    resp.raise_for_status()
                    for item in resp.json():
                        if "pull_request" in item:
                            continue
                        ref = WorkRef(source=self._id, ref=str(item["number"]))
                        found.setdefault(ref, set()).add(marker)
                    next_link = resp.links.get("next")
                    url = next_link["url"] if next_link else None
                    params = None  # the Link header's next URL already carries the query
        except httpx2.HTTPError as exc:
            _log.error("marked-refs discovery failed", source=self._name, error=str(exc))
            raise WorkAnnotateError(
                f"failed to discover marked refs for {self._name}: {exc}", retry_after=self._rate_refusal(exc)
            ) from exc
        return {ref: frozenset(markers) for ref, markers in found.items()}

    # -- IWorkCloser -----------------------------------------------------------

    def close(self, pointer: WorkRef, *, trace: DeliveryTrace | None) -> None:
        """Post the marker-deduped trace comment (if ``trace``), then ``PATCH`` closed; a 404/410 is gone."""
        if (resume_at := self._turned_away()) is not None:
            raise WorkCloseError(
                f"{self._name}#{pointer.ref}: rate budget low until {resume_at}", retry_after=resume_at
            )
        try:
            if trace is not None:
                self._post_trace_once(pointer, trace)
            resp = self._client.patch(
                f"/repos/{self._repo}/issues/{pointer.ref}",
                json={"state": "closed", "state_reason": "completed"},
            )
            if resp.status_code in (404, 410):
                raise WorkItemGoneError(f"{self._name}#{pointer.ref} no longer exists")
            resp.raise_for_status()
        except httpx2.HTTPError as exc:
            _log.error("close failed", source=self._name, ref=pointer.ref, error=str(exc))
            raise WorkCloseError(
                f"failed to close {self._name}#{pointer.ref}: {exc}", retry_after=self._rate_refusal(exc)
            ) from exc

    def _post_trace_once(self, pointer: WorkRef, trace: DeliveryTrace) -> None:
        marker = _trace_marker(trace.chunk_id)
        path = f"/repos/{self._repo}/issues/{pointer.ref}/comments"
        url: str | None = path
        params: dict[str, str | int] | None = {"per_page": 100}
        while url is not None:
            resp = self._client.get(url, params=params)
            if resp.status_code in (404, 410):
                raise WorkItemGoneError(f"{self._name}#{pointer.ref} no longer exists")
            resp.raise_for_status()
            if any(marker in (c.get("body") or "") for c in resp.json()):
                return
            next_link = resp.links.get("next")
            url = next_link["url"] if next_link else None
            params = None  # the Link header's next URL already carries the query
        resp = self._client.post(path, json={"body": _trace_comment(trace, marker)})
        if resp.status_code in (404, 410):
            raise WorkItemGoneError(f"{self._name}#{pointer.ref} no longer exists")
        resp.raise_for_status()


def _message(response: httpx2.Response) -> str:
    """The forge's ``message`` field, or the empty string when the answer carries none."""
    try:
        table = as_table(response.json()) if response.content else None
    except ValueError:
        return response.text
    message = table.get("message") if table is not None else None
    return message if isinstance(message, str) else ""


def _batch_query(pointers: Sequence[WorkRef], include_comments: bool) -> str:
    """One aliased ``issueOrPullRequest`` field per pointer under the source's repository."""
    selection = "title body" + (" comments(first: 100) { nodes { body } }" if include_comments else "")
    fields = "\n".join(
        f"i{p.ref}: issueOrPullRequest(number: {int(p.ref)}) {{ ... on Issue {{ {selection} }} "
        f"... on PullRequest {{ {selection} }} }}"
        for p in pointers
    )
    return f"query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{\n{fields}\n}} }}"


def _batch_item(name: str, pointer: WorkRef, node: object, errors: list[object]) -> WorkItem | WorkSourceError:
    """One alias's answer as an item, or the error that ref alone failed with."""
    fields = as_table(node)
    if fields is None:
        path = ["repository", f"i{pointer.ref}"]
        reason = next(
            (str(e.get("message")) for e in map(as_table, errors) if e and e.get("path") == path), "not found"
        )
        return WorkSourceError(f"failed to read {name}#{pointer.ref}: {reason}")
    thread = as_table(fields.get("comments")) or {}
    comments = [str((as_table(c) or {}).get("body") or "") for c in as_list(thread.get("nodes")) or []]
    return WorkItem(body=str(fields.get("body") or ""), title=str(fields.get("title") or ""), comments=comments)


def _trace_marker(chunk_id: str) -> str:
    return f"<!-- blizzard-delivery:{chunk_id} -->"


def _trace_comment(trace: DeliveryTrace, marker: str) -> str:
    """One line naming each landing's PR and commit; never a closing keyword."""
    landed = "; ".join(
        ", ".join(
            [
                landing.repo,
                *([f"merged {landing.pr_url}"] if landing.pr_url else []),
                f"commit {landing.commit_url or landing.commit_hash[:12]}",
            ]
        )
        for landing in trace.landings
    )
    chunk = f"chunk {trace.chunk_id}" + (f" ({trace.board_url})" if trace.board_url else "")
    return f"Delivered by blizzard {chunk}: {landed}\n\n{marker}"


_work_source_conforms: type[IWorkSource] = GitHubWorkSource


_work_batch_reader_conforms: type[IWorkSourceBatchReader] = GitHubWorkSource


_work_annotator_conforms: type[IWorkAnnotator] = GitHubWorkSource


_work_closer_conforms: type[IWorkCloser] = GitHubWorkSource
