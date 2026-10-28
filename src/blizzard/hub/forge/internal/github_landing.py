"""The GitHub REST adapter behind the landing seam's three roles (package-private).

One adapter speaks to one repository with that repository's credential. It maps GitHub's vocabulary into the seam's
words and classifies every answer into the seam's failures, retrying an unavailable forge twice — after one second,
then three — and never anything else. Its client is created on the loop that first calls it, the action loop, and
closed there: :meth:`GitHubLanding.close` is the cache's synchronous close, scheduling ``aclose()`` onto that loop."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, ClassVar, NoReturn

import httpx2

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.untyped import as_list, as_table
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.forge.internal.github_reads import KeptAnswer, KeptAnswers, KeptComparisons, is_object_id
from blizzard.hub.forge.landing import (
    Change,
    ChangeListing,
    ChangeState,
    Check,
    CheckOutcome,
    CommitPresence,
    CommitSummary,
    Comparison,
    ComparisonStatus,
    FileChange,
    ForgeNotFound,
    ForgeObject,
    ForgeRateLimited,
    ForgeRejected,
    ForgeUnavailable,
    Mergeability,
    MergeMethod,
    Update,
    UpdateMethod,
    UpdateOutcome,
)
from blizzard.hub.forge.registry import ForgeBinding

_log = get_logger("blizzard.hub.forge.github_landing")

# The user the hub names on the pull requests and merges it makes.
_HUB_USER = "blizzard-hub"

#: Bounds one attempt, as the work-source client's calls are bounded.
ATTEMPT_TIMEOUT_SECONDS = 30.0
#: The waits before the second and third attempts at an unavailable forge.
RETRY_DELAYS_SECONDS = (1.0, 3.0)
#: How long the repository probe's answer is kept.
PROBE_MEMO = timedelta(seconds=60)
#: A rate limit that names no usable instant, and the floor of a secondary limit.
_DEFAULT_RESUME = timedelta(seconds=60)

_PAGE_SIZE = 100
_PAGE_MAX = 10
#: GitHub's cap on a comparison's file list; a list this long may be cut short.
_COMPARE_FILES_MAX = 300

#: How each refusal is worded by GitHub and the mock, matched by prefix since both append detail.
_STALE_PREFIXES = ("update is not a fast forward",)
_PROTECTED_PREFIXES = (
    "protected branch update failed",
    "cannot update this protected ref",
    "repository rule violations found",
    "required status check",
    "changes must be made through a pull request",
)
_EXISTS_PREFIXES = ("reference already exists",)
_METHOD_PREFIXES = (
    "merge commits are not allowed",
    "squash merges are not allowed",
    "rebase merges are not allowed",
)

_MERGEABILITY = {
    "clean": Mergeability.MERGEABLE,
    "unstable": Mergeability.MERGEABLE,
    "blocked": Mergeability.BLOCKED,
    "behind": Mergeability.BEHIND,
    "dirty": Mergeability.CONFLICTING,
    "draft": Mergeability.DRAFT,
}
_CONCLUSION_OUTCOME = {
    "success": CheckOutcome.PASSED,
    "neutral": CheckOutcome.NEUTRAL,
    "skipped": CheckOutcome.NEUTRAL,
    "failure": CheckOutcome.FAILED,
    "timed_out": CheckOutcome.FAILED,
    "action_required": CheckOutcome.FAILED,
}


def mergeability_of(mergeable_state: object) -> Mergeability:
    """Only the states ``land`` reads checks through, merges up, or bounces on are named; every other state waits."""
    if not isinstance(mergeable_state, str):
        return Mergeability.PENDING
    return _MERGEABILITY.get(mergeable_state, Mergeability.PENDING)


def outcome_of(status: object, conclusion: object) -> CheckOutcome:
    """A completed run's conclusion decides; a cancelled, stale, unstarted, unknown, or unfinished run waits."""
    if status != "completed" or not isinstance(conclusion, str):
        return CheckOutcome.PENDING
    return _CONCLUSION_OUTCOME.get(conclusion, CheckOutcome.PENDING)


class GitHubLanding:
    #: Every capability the seam names; held equal to ``FORGE_KIND_CAPABILITIES["github"]``.
    OFFERED: ClassVar[frozenset[Capability]] = frozenset(Capability)

    def __init__(
        self,
        binding: ForgeBinding,
        *,
        clock: IClock,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        transport: httpx2.AsyncBaseTransport | None = None,
        instrument: Callable[[httpx2.AsyncClient], None] | None = None,
    ) -> None:
        self._binding = binding
        self._repo = f"/repos/{binding.owner}/{binding.repo}"
        self._clock = clock
        self._sleep = sleep
        self._transport = transport
        self._instrument = instrument
        self._client: httpx2.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closing: set[asyncio.Task[None]] = set()
        self._probed: tuple[bool, datetime] | None = None
        self._answers = KeptAnswers()
        self._comparisons = KeptComparisons()

    def close(self) -> None:
        """Schedule the client's ``aclose()`` on the loop it ran on and return at once; a loop already gone drops it."""
        client, loop = self._client, self._loop
        self._client = None
        if client is None or loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._aclose, client)
        except RuntimeError:
            _log.warning("forge client dropped unclosed: its loop has stopped", api_url=self._binding.api_url)

    def _aclose(self, client: httpx2.AsyncClient) -> None:
        task = asyncio.ensure_future(client.aclose())
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)

    # IForgeRefs

    async def branch_tip(self, branch: str) -> str | None:
        response = await self._send("GET", f"{self._repo}/git/ref/heads/{branch}")
        if response.status_code == 404 and not await self._repository_missing():
            return None
        payload = await self._answered(response, ForgeObject.BRANCH)
        table = as_table(payload)
        ref = as_table(table.get("object") or {}) if table is not None else None
        sha = ref.get("sha") if ref is not None else None
        return self._text(sha, "branch tip")

    async def compare(self, base: str, head: str) -> Comparison:
        fixed = is_object_id(base) and is_object_id(head)
        if fixed and (kept := self._comparisons.get(f"{base}...{head}")) is not None:
            return kept
        comparison = await self._compare(base, head)
        if fixed:
            self._comparisons.put(f"{base}...{head}", comparison)
        return comparison

    async def _compare(self, base: str, head: str) -> Comparison:
        payload = await self._answered(
            await self._send("GET", f"{self._repo}/compare/{base}...{head}"), ForgeObject.REVISION
        )
        try:
            status = ComparisonStatus(payload["status"])
            ahead, behind = int(payload["ahead_by"]), int(payload["behind_by"])
            total = int(payload.get("total_commits", ahead))
            commits = tuple(
                CommitSummary(c["sha"], tuple(p["sha"] for p in c.get("parents") or ())) for c in payload["commits"]
            )
            files = tuple(_file_change(f) for f in payload.get("files") or ())
            base_commit: dict[str, Any] = payload.get("merge_base_commit") or {}
            merge_base: str | None = base_commit.get("sha")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ForgeUnavailable(f"unreadable comparison {base}...{head}: {exc}") from exc
        return Comparison(
            status=status,
            ahead=ahead,
            behind=behind,
            merge_base=merge_base,
            commits=commits,
            commits_truncated=len(commits) < total,
            files=files,
            files_truncated=len(files) >= _COMPARE_FILES_MAX,
        )

    async def commit_exists(self, revision: str) -> CommitPresence:
        response = await self._send("GET", f"{self._repo}/commits/{revision}")
        if response.status_code == 404 and not await self._repository_missing():
            return CommitPresence(False)
        payload = await self._answered(response, ForgeObject.REVISION)
        return CommitPresence(True, _authored_at(payload))

    async def advance_ref(self, branch: str, expected: str, revision: str) -> None:
        if await self.branch_tip(branch) != expected:
            raise ForgeRejected(ForgeRejected.STALE, f"{branch} is not at {expected}")
        response = await self._send(
            "PATCH", f"{self._repo}/git/refs/heads/{branch}", {"sha": revision, "force": False}, write=True
        )
        if response.status_code == 422:
            self._refuse_ref(response, stale=_STALE_PREFIXES)
        await self._answered(response, ForgeObject.BRANCH)

    async def merge_into(self, branch: str, revision: str, message: str) -> str | None:
        body = {"base": branch, "head": revision, "commit_message": message, "user": _HUB_USER}
        response = await self._send("POST", f"{self._repo}/merges", body, write=True)
        if response.status_code == 204:
            return None
        if response.status_code == 409:
            raise ForgeRejected(ForgeRejected.CONFLICT, _message(response))
        if response.status_code == 422:
            self._refuse_ref(response)
        result = await self._answered(response, ForgeObject.BRANCH)
        merged = as_table(result)
        return self._text(merged.get("sha") if merged is not None else None, "merge commit")

    async def create_ref(self, branch: str, revision: str) -> None:
        response = await self._send(
            "POST", f"{self._repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": revision}, write=True
        )
        if response.status_code == 422:
            if _has_prefix(_message(response), _EXISTS_PREFIXES):
                if await self.branch_tip(branch) == revision:
                    return
                raise ForgeRejected(ForgeRejected.STALE, _message(response))
            self._refuse_ref(response)
        await self._answered(response, ForgeObject.BRANCH)

    async def delete_ref(self, branch: str) -> None:
        response = await self._send("DELETE", f"{self._repo}/git/refs/heads/{branch}", write=True)
        if response.status_code in (404, 422) and not _has_prefix(_message(response), _PROTECTED_PREFIXES):
            return
        if response.status_code == 422:
            self._refuse_ref(response)
        await self._answered(response, ForgeObject.BRANCH)

    @staticmethod
    def _refuse_ref(response: httpx2.Response, stale: tuple[str, ...] = ()) -> NoReturn:
        """Raise the named reason a 422's message carries, or the forge's own words."""
        message = _message(response)
        if _has_prefix(message, stale):
            raise ForgeRejected(ForgeRejected.STALE, message)
        if _has_prefix(message, _PROTECTED_PREFIXES):
            raise ForgeRejected(ForgeRejected.PROTECTED, message)
        raise ForgeRejected(message or "HTTP 422")

    # IForgeChanges

    async def changes(self, state: ChangeListing, base: str, head: str | None = None) -> tuple[Change, ...]:
        query = f"state={state}&base={base}&sort=created&direction=desc&per_page={_PAGE_SIZE}"
        if head is not None:
            query += f"&head={self._binding.owner}:{head}"
        found: list[Change] = []
        for page in range(1, _PAGE_MAX + 1):
            listed = await self._read("GET", f"{self._repo}/pulls?{query}&page={page}", ForgeObject.REPOSITORY)
            pulls = as_list(listed)
            if pulls is None:
                raise ForgeUnavailable(f"unreadable pull request list into {base}")
            found.extend(
                change
                for change in (_change(pull) for pull in pulls)
                if change.base == base and (head is None or change.head == head)
            )
            if len(pulls) < _PAGE_SIZE:
                break
        return tuple(found)

    async def recently_closed(self, base: str, limit: int) -> tuple[Change, ...]:
        query = f"state=closed&base={base}&sort=updated&direction=desc&per_page={limit}"
        listed = await self._read("GET", f"{self._repo}/pulls?{query}", ForgeObject.REPOSITORY)
        pulls = as_list(listed)
        if pulls is None:
            raise ForgeUnavailable(f"unreadable pull request list into {base}")
        return tuple(change for change in (_change(pull) for pull in pulls) if change.base == base)[:limit]

    async def open_change(self, head: str, base: str, title: str, body: str) -> Change:
        body_json = {"title": title, "head": head, "base": base, "body": body, "user": _HUB_USER}
        return _change(await self._read("POST", f"{self._repo}/pulls", ForgeObject.BRANCH, body_json, write=True))

    async def change(self, number: int) -> Change:
        return _change(await self._read("GET", f"{self._repo}/pulls/{number}", ForgeObject.CHANGE))

    async def update_from_base(self, number: int, expected_head: str) -> Update:
        response = await self._send(
            "PUT", f"{self._repo}/pulls/{number}/update-branch", {"expected_head_sha": expected_head}, write=True
        )
        status = response.status_code
        if status < 400:
            return Update(UpdateOutcome.ACCEPTED, UpdateMethod.MERGE)
        if status in (401, 403, 404):
            await self._fail(response, ForgeObject.CHANGE)
        if status == 422 and "conflict" in _message(response).lower():
            return Update(UpdateOutcome.CONFLICT, UpdateMethod.MERGE)
        return Update(UpdateOutcome.REFUSED, UpdateMethod.MERGE)

    async def merge(self, number: int, expected_head: str, method: MergeMethod, message: str) -> str:
        body = {"commit_message": message, "sha": expected_head, "merge_method": str(method), "user": _HUB_USER}
        response = await self._send("PUT", f"{self._repo}/pulls/{number}/merge", body, write=True)
        if response.status_code == 405 and _has_prefix(_message(response), _METHOD_PREFIXES):
            raise ForgeRejected(ForgeRejected.METHOD, _message(response))
        result = await self._answered(response, ForgeObject.CHANGE)
        merged = as_table(result)
        if merged is not None and merged.get("merged") and isinstance(sha := merged.get("sha"), str) and sha:
            return sha
        landed = await self.change(number)
        if landed.merged_revision is None:
            raise ForgeRejected(_message(response) or f"pull request {number} did not merge")
        return landed.merged_revision

    # IForgeChecks

    async def checks(self, ref: str) -> tuple[Check, ...]:
        found: list[Check] = []
        for page in range(1, _PAGE_MAX + 1):
            payload = await self._answered(
                await self._send("GET", f"{self._repo}/commits/{ref}/check-runs?per_page={_PAGE_SIZE}&page={page}"),
                ForgeObject.REVISION,
            )
            table = as_table(payload)
            runs = as_list(table.get("check_runs")) if table is not None else None
            if runs is None:
                raise ForgeUnavailable(f"unreadable check runs on {ref}")
            for run in runs:
                entry = as_table(run)
                if entry is None:
                    raise ForgeUnavailable(f"unreadable check runs on {ref}")
                found.append(_check(entry))
            if len(runs) < _PAGE_SIZE:
                break
        return tuple(found)

    async def change_checks(self, number: int) -> tuple[Check, ...]:
        return await self.checks((await self.change(number)).head_revision)

    async def rerun_check(self, id: str) -> bool:
        # A GitHub Actions check run's id is its job id, and a token can re-run a job; the check-run rerequest
        # route answers only the GitHub App that owns the run. A run that is no Actions job answers 404 or 422.
        response = await self._send("POST", f"{self._repo}/actions/jobs/{id}/rerun", write=True)
        if response.status_code in (401, 403, 404, 422):
            return False
        await self._answered(response, ForgeObject.CHECK)
        return True

    # Answers

    async def _read(
        self, method: str, path: str, missing: ForgeObject, body: dict[str, Any] | None = None, *, write: bool = False
    ) -> Any:
        return await self._answered(await self._send(method, path, body, write=write), missing)

    async def _answered(self, response: httpx2.Response, missing: ForgeObject) -> Any:
        """A 2xx answer's body, or the failure any other answer is — a 404 naming ``missing`` unless the probe finds
        the repository gone."""
        if response.status_code < 300:
            return _body(response)
        await self._fail(response, missing)

    async def _fail(self, response: httpx2.Response, missing: ForgeObject | None) -> NoReturn:
        """Raise the failure a non-2xx answer is; ``missing`` of ``None`` reads a 404 as any other refusal."""
        status = response.status_code
        if status in (401, 403):
            raise ForgeRejected(ForgeRejected.AUTH)
        if status == 404 and missing is not None:
            raise ForgeNotFound(ForgeObject.REPOSITORY if await self._repository_missing() else missing)
        raise ForgeRejected(_message(response) or f"HTTP {status}")

    async def _repository_missing(self) -> bool:
        """Whether the repository itself answers 404 — read at most once a minute."""
        now = self._clock.now()
        if self._probed is not None and now - self._probed[1] < PROBE_MEMO:
            return self._probed[0]
        response = await self._send("GET", self._repo)
        if response.status_code not in (200, 404):
            await self._fail(response, None)
        missing = response.status_code == 404
        self._probed = (missing, now)
        return missing

    # Transport

    async def _send(
        self, method: str, path: str, body: dict[str, Any] | None = None, *, write: bool = False
    ) -> httpx2.Response:
        """One call's answer, retried while the forge is unavailable. A write holds the credential's lock around each
        attempt, never through the wait between them. A 4xx is returned for the operation to read; a 2xx body that
        needs interpreting is too."""
        for delay in (*RETRY_DELAYS_SECONDS, None):
            try:
                if write:
                    async with self._binding.write_lock():
                        return await self._attempt(method, path, body)
                if method == "GET":
                    return await self._read_conditionally(path)
                return await self._attempt(method, path, body)
            except ForgeRateLimited:
                raise
            except ForgeUnavailable:
                if delay is None:
                    raise
                await self._sleep(delay)
        raise AssertionError("unreachable")

    async def _read_conditionally(self, path: str) -> httpx2.Response:
        """A ``GET`` that sends the kept answer's ``ETag``; a ``304`` is answered from what was kept, and one with
        nothing kept to answer from is asked again unconditionally."""
        kept = self._answers.get(path)
        response = await self._attempt("GET", path, None, {"If-None-Match": kept.etag} if kept else None)
        if response.status_code == 304:
            if kept is not None:
                return _kept_response(kept, response)
            self._answers.drop(path)
            response = await self._attempt("GET", path, None)
        etag = response.headers.get("etag")
        if response.status_code < 300 and etag:
            self._answers.put(path, KeptAnswer(etag, response.status_code, response.content))
        return response

    async def _attempt(
        self, method: str, path: str, body: dict[str, Any] | None, headers: dict[str, str] | None = None
    ) -> httpx2.Response:
        try:
            response = await self._http().request(
                method,
                path,
                content=json.dumps(body).encode() if body is not None else None,
                headers=headers,
                timeout=ATTEMPT_TIMEOUT_SECONDS,
            )
        except httpx2.HTTPError as exc:
            raise ForgeUnavailable(f"{method} {self._binding.api_url}{path}: {exc}") from exc
        if response.status_code >= 500:
            raise ForgeUnavailable(f"{method} {self._binding.api_url}{path}: HTTP {response.status_code}")
        if response.status_code in (403, 429):
            resume_at = self._resume_at(response)
            if resume_at is not None:
                raise ForgeRateLimited(resume_at, f"{method} {self._binding.api_url}{path}: rate limited")
        return response

    def _resume_at(self, response: httpx2.Response) -> datetime | None:
        """When a rate-limited answer says to come back, or ``None`` when the answer is no rate limit."""
        headers = response.headers
        secondary = "secondary rate limit" in _message(response).lower()
        retry_after = headers.get("retry-after")
        if headers.get("x-ratelimit-remaining") != "0" and retry_after is None and not secondary:
            return None
        now = self._clock.now()
        resume = _seconds_from(now, retry_after) or _reset_at(now, headers.get("x-ratelimit-reset"))
        resume = resume or now + _DEFAULT_RESUME
        return max(resume, now + _DEFAULT_RESUME) if secondary else resume

    def _http(self) -> httpx2.AsyncClient:
        if self._client is None:
            headers = {
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "Authorization": f"token {self._binding.token}",
            }
            self._loop = asyncio.get_running_loop()
            self._client = httpx2.AsyncClient(
                base_url=self._binding.api_url.rstrip("/"), headers=headers, transport=self._transport
            )
            if self._instrument is not None:
                self._instrument(self._client)
        return self._client

    @staticmethod
    def _text(value: object, what: str) -> str:
        if not isinstance(value, str) or not value:
            raise ForgeUnavailable(f"unreadable {what}")
        return value


def _has_prefix(message: str, prefixes: tuple[str, ...]) -> bool:
    return message.lower().startswith(prefixes)


def _kept_response(kept: KeptAnswer, answered: httpx2.Response) -> httpx2.Response:
    """The kept answer, standing in for the ``304`` that confirmed it still holds."""
    return httpx2.Response(kept.status_code, content=kept.content, request=answered.request)


def _body(response: httpx2.Response) -> Any:
    if not response.content:
        return None
    try:
        return json.loads(response.content)
    except ValueError:
        return {"message": response.content.decode(errors="replace")}


def _message(response: httpx2.Response) -> str:
    body = _body(response)
    table = as_table(body)
    message = table.get("message") if table is not None else None
    return message if isinstance(message, str) else ""


def _seconds_from(now: datetime, raw: str | None) -> datetime | None:
    try:
        seconds = int(raw) if raw is not None else 0
    except ValueError:
        return None
    return now + timedelta(seconds=seconds) if seconds > 0 else None


def _reset_at(now: datetime, raw: str | None) -> datetime | None:
    try:
        reset = datetime.fromtimestamp(int(raw), tz=now.tzinfo) if raw is not None else None
    except (ValueError, OverflowError, OSError):
        return None
    return reset if reset is not None and reset > now else None


def _authored_at(payload: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(payload["commit"]["author"]["date"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _change(pull: Any) -> Change:
    try:
        merged = bool(pull.get("merged") or pull.get("merged_at"))
        state = ChangeState.OPEN if pull["state"] == "open" else ChangeState.MERGED if merged else ChangeState.CLOSED
        return Change(
            number=int(pull["number"]),
            head=pull["head"]["ref"],
            head_revision=pull["head"]["sha"],
            base=pull["base"]["ref"],
            base_revision=pull["base"].get("sha") or "",
            state=state,
            mergeability=mergeability_of(pull.get("mergeable_state")),
            web_url=pull.get("html_url") or "",
            merged_revision=(pull.get("merge_commit_sha") or None) if merged else None,
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ForgeUnavailable(f"unreadable pull request: {exc}") from exc


def _check(run: dict[str, Any]) -> Check:
    conclusion = run.get("conclusion")
    return Check(
        id=str(run.get("id", "")),
        name=str(run.get("name", "")),
        outcome=outcome_of(run.get("status"), conclusion),
        detail=conclusion if isinstance(conclusion, str) else str(run.get("status") or ""),
        details_url=run.get("details_url") if isinstance(run.get("details_url"), str) else None,
    )


def _file_change(entry: dict[str, Any]) -> FileChange:
    return FileChange(
        path=entry["filename"],
        previous_path=entry.get("previous_filename"),
        status=entry.get("status") or "",
        blob=entry.get("sha"),
        patch=entry.get("patch"),
        additions=int(entry.get("additions") or 0),
        deletions=int(entry.get("deletions") or 0),
    )
