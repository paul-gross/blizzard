"""The GitHub-shaped work-source binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.hub.work_sources.source.IWorkSource` against a GitHub REST
v3 surface. Confined to ``internal/`` (``bzh:dependency-inversion``); ``httpx`` is used
only here. One instance per work-source record revision, pinned to its own ``repo``
and ``web_base`` and carrying its own credentialed client."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

import httpx

from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import adapter_model
from blizzard.hub.domain.chunk.delivery_read import DeliveryTrace
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.work_sources.annotator import IWorkAnnotator, WorkAnnotateError, WorkStatusMarker
from blizzard.hub.work_sources.closer import IWorkCloser, WorkCloseError, WorkItemGoneError
from blizzard.hub.work_sources.source import IWorkSource, WorkItem, WorkSourceError

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

    def __init__(self, client: httpx.Client, *, name: str, repo: str, web_base: str) -> None:
        self._client = client
        self._name = name
        self._repo = repo
        self._web_base = web_base.rstrip("/")
        self._labels_bootstrapped = False

    def parse(self, token: str) -> WorkRef | None:
        """This source's own ingest-token forms into a pointer, or ``None`` when ``token``
        isn't shaped for it: ``{name}:{number}``, ``{name}#{number}``, or an issue URL
        (full or schemeless) naming *this binding's own configured* ``repo``."""
        for sep_char in (":", "#"):
            prefix, sep, ref = token.partition(sep_char)
            if sep and prefix == self._name and ref.isdigit():
                return WorkRef(source=self._name, ref=ref)
        match = _ISSUE_URL_RE.search(token)
        if match is not None and f"{match['owner']}/{match['repo']}" == self._repo:
            return WorkRef(source=self._name, ref=match["number"])
        return None

    def fetch(self, pointer: WorkRef) -> WorkItem:
        base = f"/repos/{self._repo}/issues/{pointer.ref}"
        try:
            issue = self._client.get(base)
            issue.raise_for_status()
            comments = self._client.get(f"{base}/comments")
            comments.raise_for_status()
        except httpx.HTTPError as exc:
            _log.error("work-item fetch failed", source=pointer.source, ref=pointer.ref, error=str(exc))
            raise WorkSourceError(f"failed to read {self._name}#{pointer.ref}: {exc}") from exc
        return WorkItem(
            body=str(issue.json().get("body") or ""),
            title=str(issue.json().get("title") or ""),
            comments=[str(c.get("body") or "") for c in comments.json()],
        )

    def label(self, pointer: WorkRef) -> str | None:
        """``{name}#{ref}`` — always renders; ``ref`` is opaque here."""
        return f"{self._name}#{pointer.ref}"

    def web_url(self, pointer: WorkRef, *, live_holder: str | None) -> str | None:
        """This source's address never depends on which chunk holds the pointer, so
        ``live_holder`` is ignored."""
        return f"{self._web_base}/{self._repo}/issues/{pointer.ref}"

    def forge_reference(self, pointer: WorkRef) -> str | None:
        """``{owner}/{repo}#{ref}`` from the configured ``repo``, not the ``name`` alias."""
        return f"{self._repo}#{pointer.ref}"

    def branch_url(self, repo: str, branch_name: str) -> str | None:
        """The forge ``tree`` URL for ``branch_name`` on ``repo`` — an owner-less repo (a
        produced artifact names its repo by the worktree dir alone) is qualified with this
        source's own repo's owner; an already ``owner/name`` repo passes through."""
        repo_path = repo if "/" in repo else f"{self._owner}/{repo}"
        return f"{self._web_base}/{repo_path}/tree/{branch_name}"

    @property
    def _owner(self) -> str:
        return self._repo.split("/", 1)[0]

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
            except httpx.HTTPError as exc:
                _log.error("label bootstrap failed", source=self._name, label=label.name, error=str(exc))
                raise WorkAnnotateError(f"failed to bootstrap label {label.name!r} for {self._name}: {exc}") from exc
        self._labels_bootstrapped = True

    def set_status(self, pointer: WorkRef, marker: WorkStatusMarker) -> None:
        """Add ``marker``'s label and remove the other one if present — exclusive
        and idempotent, mirroring GitHub's own idempotent label add/remove."""
        self._ensure_labels_bootstrapped()
        try:
            added = self._client.post(f"/repos/{self._repo}/issues/{pointer.ref}/labels", json=[Label(marker).name])
            added.raise_for_status()
            other = Label(marker).excluded.name
            resp = self._client.delete(f"/repos/{self._repo}/issues/{pointer.ref}/labels/{other}")
            if resp.status_code != 404:
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            _log.error("set-status failed", source=self._name, ref=pointer.ref, marker=marker.value, error=str(exc))
            raise WorkAnnotateError(f"failed to set {marker.value} on {self._name}#{pointer.ref}: {exc}") from exc

    def clear_status(self, pointer: WorkRef) -> None:
        """Remove every marker label from ``pointer`` — a 404 (label already
        absent) is the expected steady state, not a failure."""
        self._ensure_labels_bootstrapped()
        try:
            for marker in WorkStatusMarker:
                resp = self._client.delete(f"/repos/{self._repo}/issues/{pointer.ref}/labels/{Label(marker).name}")
                if resp.status_code != 404:
                    resp.raise_for_status()
        except httpx.HTTPError as exc:
            _log.error("clear-status failed", source=self._name, ref=pointer.ref, error=str(exc))
            raise WorkAnnotateError(f"failed to clear status on {self._name}#{pointer.ref}: {exc}") from exc

    def marked_refs(self) -> dict[WorkRef, frozenset[WorkStatusMarker]]:
        """Every ref this repo currently labels, discovered per marker via a
        paginated, ``state=all`` issue listing — closed issues can still carry a
        stale label, and GitHub's default page size would silently strand labels
        past the first page. Pull-request entries (GitHub's issue-list endpoint
        returns both) are filtered out."""
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
                        ref = WorkRef(source=self._name, ref=str(item["number"]))
                        found.setdefault(ref, set()).add(marker)
                    next_link = resp.links.get("next")
                    url = next_link["url"] if next_link else None
                    params = None  # the Link header's next URL already carries the query
        except httpx.HTTPError as exc:
            _log.error("marked-refs discovery failed", source=self._name, error=str(exc))
            raise WorkAnnotateError(f"failed to discover marked refs for {self._name}: {exc}") from exc
        return {ref: frozenset(markers) for ref, markers in found.items()}

    # -- IWorkCloser -----------------------------------------------------------

    def close(self, pointer: WorkRef, *, trace: DeliveryTrace | None) -> None:
        """Post the marker-deduped trace comment (if ``trace``), then ``PATCH`` closed; a 404/410 is gone."""
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
        except httpx.HTTPError as exc:
            _log.error("close failed", source=self._name, ref=pointer.ref, error=str(exc))
            raise WorkCloseError(f"failed to close {self._name}#{pointer.ref}: {exc}") from exc

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


def _conforms_work_source(x: GitHubWorkSource) -> IWorkSource:
    return x


def _conforms_work_annotator(x: GitHubWorkSource) -> IWorkAnnotator:
    return x


def _conforms_work_closer(x: GitHubWorkSource) -> IWorkCloser:
    return x
