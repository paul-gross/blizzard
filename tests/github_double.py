"""An in-memory GitHub forge standing behind ``IGitHubHandle``, for ``land``'s component tests.

One :class:`GitHubDouble` is one forge holding any number of repositories (:class:`DoubleRepo`), each a
small model of the pull-request lifecycle ``land`` drives: a pushed branch opens a PR, a PR reports its
repository's ``mergeable_state``, a merge closes it with a merge commit. Every answer can be overridden
per call through :attr:`DoubleRepo.replies`, and :attr:`GitHubDouble.calls` logs every call made."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from blizzard.hub.delivery.forge_handle import ForgeReply, ForgeUnreachable, IGitHubHandle
from blizzard.hub.domain.config.repositories import ResolvedRepository

GREEN = [{"id": 1, "name": "build", "status": "completed", "conclusion": "success"}]

#: A per-call override: a fixed reply, an exception to raise, or a function of the call's arguments.
type Override = ForgeReply | Exception | Callable[..., ForgeReply]


@dataclass
class DoubleRepo:
    """One repository on the double: its base branch and tip, its pushed branches, its pulls, and what its
    check runs, compares and update-branch answer."""

    base_branch: str = "main"
    base_tip: str = "base-tip"
    heads: dict[str, str] = field(default_factory=dict)
    pulls: dict[int, dict[str, Any]] = field(default_factory=dict)
    mergeable_state: str = "clean"
    #: Each ref's check runs; a ref not named here is green, and ``None`` answers a 500.
    checks: dict[str, list[dict[str, Any]] | None] = field(default_factory=dict)
    #: Each ``(base, head)`` comparison; one not named here answers a 404.
    compares: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    update_reply: ForgeReply = field(default_factory=lambda: ForgeReply(202, {"message": "Updating"}))
    #: Overrides by call name (``merge``, ``open_pull``, …), each consumed in order; the last one stays.
    replies: dict[str, list[Override]] = field(default_factory=dict)
    #: Called with the PR number as a merge lands, before it answers.
    on_merge: Callable[[int], None] | None = None


class GitHubDouble:
    def __init__(self, repos: dict[str, DoubleRepo] | None = None) -> None:
        self.repos: dict[str, DoubleRepo] = repos or {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.opened = 0
        self.closed = 0
        self.tokens: dict[str, str] = {}

    def handle(self, target: ResolvedRepository, token: str) -> IGitHubHandle:
        """The ``ForgeHandleFactory`` a hub under test builds each repository's handle with."""
        self.tokens[target.record] = token
        return _Handle(self)

    def repo(self, name: str) -> DoubleRepo:
        return self.repos.setdefault(name, DoubleRepo())

    def named(self, call: str) -> list[tuple[str, dict[str, Any]]]:
        """Each call of ``call`` made, as its repository and its arguments."""
        return [(repo, args) for name, repo, args in self.calls if name == call]

    def writes(self) -> list[str]:
        """The names of every forge write made."""
        return [name for name, _, _ in self.calls if name in {"open_pull", "update_branch", "merge", "rerequest"}]

    def answer(self, call: str, repo: str, default: Callable[[], ForgeReply], **args: Any) -> ForgeReply:
        self.calls.append((call, repo, args))
        queued = self.repo(repo).replies.get(call)
        if not queued:
            return default()
        override = queued.pop(0) if len(queued) > 1 else queued[0]
        if isinstance(override, Exception):
            raise override
        if isinstance(override, ForgeReply):
            return override
        return override(**args)


def pull_body(repo: str, number: int, *, branch: str, sha: str, base: str, **extra: Any) -> dict[str, Any]:
    return {
        "number": number,
        "html_url": f"https://github.example/{repo}/pull/{number}",
        "head": {"ref": branch, "sha": sha},
        "base": {"ref": base},
        "merged": False,
        "merged_at": None,
        **extra,
    }


class _Handle:
    """One repository record's handle: every call lands on the shared double."""

    def __init__(self, double: GitHubDouble) -> None:
        self._double = double

    async def open(self) -> None:
        self._double.opened += 1

    async def close(self) -> None:
        self._double.closed += 1

    def _repo(self, repo: str) -> DoubleRepo:
        return self._double.repo(repo)

    async def closed_pulls(self, repo: str, *, base: str, page: int) -> ForgeReply:
        def default() -> ForgeReply:
            closed = [p for p in self._repo(repo).pulls.values() if p.get("merged_at") is not None and page == 1]
            return ForgeReply(200, [p for p in closed if p["base"]["ref"] == base])

        return self._double.answer("closed_pulls", repo, default, base=base, page=page)

    async def open_pulls(self, repo: str) -> ForgeReply:
        def default() -> ForgeReply:
            return ForgeReply(200, [p for p in self._repo(repo).pulls.values() if p.get("merged_at") is None])

        return self._double.answer("open_pulls", repo, default)

    async def open_pull(self, repo: str, *, title: str, head: str, base: str, body: str) -> ForgeReply:
        def default() -> ForgeReply:
            state = self._repo(repo)
            if head not in state.heads:
                return ForgeReply(422, {"message": "Validation Failed"})
            number = len(state.pulls) + 1
            state.pulls[number] = pull_body(repo, number, branch=head, sha=state.heads[head], base=base)
            return ForgeReply(201, state.pulls[number])

        return self._double.answer("open_pull", repo, default, title=title, head=head, base=base, body=body)

    async def pull(self, repo: str, number: int) -> ForgeReply:
        def default() -> ForgeReply:
            state = self._repo(repo)
            pull = state.pulls.get(number)
            if pull is None:
                return ForgeReply(404, {"message": "Not Found"})
            live = dict(pull)
            if not live["merged"]:
                live["mergeable_state"] = state.mergeable_state
                if (sha := state.heads.get(live["head"]["ref"])) is not None:
                    live["head"] = {**live["head"], "sha": sha}
            return ForgeReply(200, live)

        return self._double.answer("pull", repo, default, number=number)

    async def update_branch(self, repo: str, number: int, *, expected_head_sha: str) -> ForgeReply:
        return self._double.answer(
            "update_branch",
            repo,
            lambda: self._repo(repo).update_reply,
            number=number,
            expected_head_sha=expected_head_sha,
        )

    async def merge(self, repo: str, number: int, *, sha: str, commit_message: str, method: str) -> ForgeReply:
        def default() -> ForgeReply:
            state = self._repo(repo)
            pull = state.pulls[number]
            merge_commit = f"merge-{repo}-{number}"
            pull.update(merged=True, merged_at="2026-10-23T00:00:00Z", merge_commit_sha=merge_commit)
            pull["head"] = {**pull["head"], "sha": sha}
            state.base_tip = merge_commit
            if state.on_merge is not None:
                state.on_merge(number)
            return ForgeReply(200, {"merged": True, "sha": merge_commit})

        return self._double.answer(
            "merge", repo, default, number=number, sha=sha, commit_message=commit_message, method=method
        )

    async def check_runs(self, repo: str, ref: str) -> ForgeReply:
        def default() -> ForgeReply:
            runs = self._repo(repo).checks.get(ref, GREEN)
            if runs is None:
                return ForgeReply(500, {"message": "Server Error"})
            return ForgeReply(200, {"check_runs": runs})

        return self._double.answer("check_runs", repo, default, ref=ref)

    async def rerequest_check_run(self, repo: str, check_id: object) -> ForgeReply:
        return self._double.answer("rerequest", repo, lambda: ForgeReply(201, {}), check_id=check_id)

    async def compare(self, repo: str, base: str, head: str) -> ForgeReply:
        def default() -> ForgeReply:
            found = self._repo(repo).compares.get((base, head))
            return ForgeReply(200, found) if found is not None else ForgeReply(404, {"message": "Not Found"})

        return self._double.answer("compare", repo, default, base=base, head=head)

    async def branch(self, repo: str, branch: str) -> ForgeReply:
        def default() -> ForgeReply:
            state = self._repo(repo)
            sha = state.base_tip if branch == state.base_branch else state.heads.get(branch)
            if sha is None:
                return ForgeReply(404, {"message": "Not Found"})
            return ForgeReply(200, {"object": {"sha": sha}})

        return self._double.answer("branch", repo, default, branch=branch)


def unreachable() -> ForgeUnreachable:
    return ForgeUnreachable("connection refused")
