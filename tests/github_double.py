"""An in-memory forge behind the landing seam's three roles, for ``land``'s component tests.

One :class:`GitHubDouble` holds any number of repositories (:class:`DoubleRepo`), each a small model of the change
lifecycle ``land`` drives. Every answer can be overridden per call through :attr:`DoubleRepo.replies`, and
:attr:`GitHubDouble.calls` logs every call; a hub under test builds adapters on it through :meth:`GitHubDouble.forges`."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from blizzard.hub.composition import ForgeRegistryFactory, LiveConfig, TenantStores
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.forges import FORGE_KIND_CAPABILITIES
from blizzard.hub.domain.config.repositories import RepositoryId
from blizzard.hub.forge.budget import ForgeBudgets
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
    ForgeRejected,
    ForgeUnavailable,
    Mergeability,
    MergeMethod,
    RepositoryForge,
    Update,
    UpdateMethod,
    UpdateOutcome,
)
from blizzard.hub.forge.registry import ForgeBinding, ForgeRegistry, IForgeAdapter


def check(
    name: str = "build",
    outcome: CheckOutcome = CheckOutcome.PASSED,
    detail: str = "success",
    *,
    id: str = "1",
    details_url: str | None = None,
) -> Check:
    return Check(id=id, name=name, outcome=outcome, detail=detail, details_url=details_url)


GREEN = (check(),)
#: A failed check the base does not share — what turns a head red so ``land`` answers ``failure``.
RED = (check(outcome=CheckOutcome.FAILED, detail="failure", details_url="https://ci/1"),)


def change(
    repo: str,
    number: int,
    *,
    branch: str,
    sha: str,
    base: str,
    state: ChangeState = ChangeState.OPEN,
    mergeability: Mergeability = Mergeability.PENDING,
    merged_revision: str | None = None,
) -> Change:
    return Change(
        number=number,
        head=branch,
        head_revision=sha,
        base=base,
        base_revision="",
        state=state,
        mergeability=mergeability,
        web_url=f"https://github.example/{repo}/pull/{number}",
        merged_revision=merged_revision,
    )


def comparison(
    status: ComparisonStatus = ComparisonStatus.AHEAD,
    *,
    commits: tuple[CommitSummary, ...] = (),
    files: tuple[FileChange, ...] = (),
    merge_base: str | None = None,
    commits_truncated: bool = False,
    files_truncated: bool = False,
) -> Comparison:
    return Comparison(
        status=status,
        ahead=len(commits),
        behind=0,
        merge_base=merge_base,
        commits=commits,
        commits_truncated=commits_truncated,
        files=files,
        files_truncated=files_truncated,
    )


#: A per-call override: a fixed answer, an exception to raise, or a function of the call's arguments.
type Override = Any


@dataclass
class DoubleRepo:
    """One repository on the double: its base branch and tip, its pushed branches, its changes, and what its
    checks, comparisons and updates answer."""

    base_branch: str = "main"
    #: ``None`` reads as a base branch that does not exist.
    base_tip: str | None = "base-tip"
    heads: dict[str, str] = field(default_factory=dict)
    changes: dict[int, Change] = field(default_factory=dict)
    mergeability: Mergeability = Mergeability.MERGEABLE
    #: Each ref's checks; a ref not named here is green, and ``None`` is a forge that does not answer.
    checks: dict[str, tuple[Check, ...] | None] = field(default_factory=dict)
    #: Each ``(base, head)`` comparison; one not named here is a revision the forge does not know.
    compares: dict[tuple[str, str], Comparison] = field(default_factory=dict)
    update: Update = field(default_factory=lambda: Update(UpdateOutcome.ACCEPTED, UpdateMethod.MERGE))
    #: Overrides by call name (``merge``, ``open_change``, …), each consumed in order; the last one stays.
    replies: dict[str, list[Override]] = field(default_factory=dict)
    #: Called with the change number as a merge lands, before it answers.
    on_merge: Callable[[int], None] | None = None


_WRITES = frozenset(
    {"open_change", "update_from_base", "merge", "rerun_check", "advance_ref", "merge_into", "create_ref", "delete_ref"}
)


class GitHubDouble:
    def __init__(self, repos: dict[str, DoubleRepo] | None = None) -> None:
        self.repos: dict[str, DoubleRepo] = repos or {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        #: Each ``owner/repo`` an adapter was built for, and the credential it was built with.
        self.tokens: dict[str, str] = {}
        self.closed = 0

    def adapter(self, binding: ForgeBinding) -> IForgeAdapter:
        """The registry's build callable: one repository's adapter, every call landing on this double."""
        repo = f"{binding.owner}/{binding.repo}"
        self.tokens[repo] = binding.token
        return _Adapter(self, repo)

    def forges(self, stores: TenantStores, live: LiveConfig, budgets: ForgeBudgets) -> ForgeRegistry:
        """The :data:`ForgeRegistryFactory` a hub under test builds each tenant's registry with."""
        return self.forges_offering(FORGE_KIND_CAPABILITIES)(stores, live, budgets)

    def forges_offering(self, capabilities: Mapping[str, frozenset[Capability]]) -> ForgeRegistryFactory:
        """A registry factory whose forges offer only ``capabilities``, by kind."""

        def build(stores: TenantStores, live: LiveConfig, budgets: ForgeBudgets) -> ForgeRegistry:
            return ForgeRegistry(
                records=stores.repository_records,
                forges=stores.forge_records,
                objects=live.objects,
                secrets=live.secrets,
                build=self.adapter,
                capabilities=capabilities,
                budgets=budgets,
            )

        return build

    def forge(
        self, repository: str, repo: str, offered: frozenset[Capability] = frozenset(Capability)
    ) -> RepositoryForge:
        """``repo``'s roles bundled as a visit hands them to ``land``, with no registry between."""
        adapter = _Adapter(self, repo)
        return RepositoryForge(RepositoryId(repository), adapter, adapter, adapter, offered)

    def repo(self, name: str) -> DoubleRepo:
        return self.repos.setdefault(name, DoubleRepo())

    def named(self, call: str) -> list[tuple[str, dict[str, Any]]]:
        """Each call of ``call`` made, as its repository and its arguments."""
        return [(repo, args) for name, repo, args in self.calls if name == call]

    def writes(self) -> list[str]:
        """The names of every forge write made."""
        return [name for name, _, _ in self.calls if name in _WRITES]

    def answer(self, call: str, repo: str, default: Callable[[], Any], **args: Any) -> Any:
        self.calls.append((call, repo, args))
        queued = self.repo(repo).replies.get(call)
        if not queued:
            return default()
        override = queued.pop(0) if len(queued) > 1 else queued[0]
        if isinstance(override, BaseException):
            raise override
        if callable(override):
            return override(**args)
        return override


class _Adapter:
    """One repository's adapter: every call lands on the shared double."""

    def __init__(self, double: GitHubDouble, repo: str) -> None:
        self._double = double
        self._repo = repo

    def close(self) -> None:
        self._double.closed += 1

    @property
    def _state(self) -> DoubleRepo:
        return self._double.repo(self._repo)

    def _live(self, found: Change) -> Change:
        """An open change as the forge reads it now: its repository's mergeability, its branch's tip."""
        if found.state is not ChangeState.OPEN:
            return found
        state = self._state
        return replace(
            found, mergeability=state.mergeability, head_revision=state.heads.get(found.head, found.head_revision)
        )

    async def branch_tip(self, branch: str) -> str | None:
        def default() -> str | None:
            state = self._state
            return state.base_tip if branch == state.base_branch else state.heads.get(branch)

        return self._double.answer("branch_tip", self._repo, default, branch=branch)

    async def compare(self, base: str, head: str) -> Comparison:
        def default() -> Comparison:
            found = self._state.compares.get((base, head))
            if found is None:
                raise ForgeNotFound(ForgeObject.REVISION)
            return found

        return self._double.answer("compare", self._repo, default, base=base, head=head)

    async def commit_exists(self, revision: str) -> CommitPresence:
        return self._double.answer("commit_exists", self._repo, lambda: CommitPresence(True), revision=revision)

    async def advance_ref(self, branch: str, expected: str, revision: str) -> None:
        def default() -> None:
            state = self._state
            if state.heads.get(branch) != expected:
                raise ForgeRejected(ForgeRejected.STALE)
            state.heads[branch] = revision

        self._double.answer("advance_ref", self._repo, default, branch=branch, expected=expected, revision=revision)

    async def merge_into(self, branch: str, revision: str, message: str) -> str | None:
        return self._double.answer(
            "merge_into", self._repo, lambda: None, branch=branch, revision=revision, message=message
        )

    async def create_ref(self, branch: str, revision: str) -> None:
        def default() -> None:
            self._state.heads.setdefault(branch, revision)

        self._double.answer("create_ref", self._repo, default, branch=branch, revision=revision)

    async def delete_ref(self, branch: str) -> None:
        self._double.answer("delete_ref", self._repo, lambda: self._state.heads.pop(branch, None), branch=branch)

    async def recently_closed(self, base: str, limit: int) -> tuple[Change, ...]:
        def default() -> tuple[Change, ...]:
            closed = (c for c in self._state.changes.values() if c.base == base and c.state is not ChangeState.OPEN)
            return tuple(sorted(closed, key=lambda c: -c.number))[:limit]

        return self._double.answer("recently_closed", self._repo, default, base=base, limit=limit)

    async def changes(self, state: ChangeListing, base: str, head: str | None = None) -> tuple[Change, ...]:
        def default() -> tuple[Change, ...]:
            wanted = {ChangeState.OPEN} if state is ChangeListing.OPEN else {ChangeState.MERGED, ChangeState.CLOSED}
            return tuple(
                self._live(c)
                for c in sorted(self._state.changes.values(), key=lambda c: -c.number)
                if c.state in wanted and c.base == base and (head is None or c.head == head)
            )

        return self._double.answer("changes", self._repo, default, state=state, base=base, head=head)

    async def open_change(self, head: str, base: str, title: str, body: str) -> Change:
        def default() -> Change:
            state = self._state
            if head not in state.heads:
                raise ForgeRejected("Validation Failed")
            number = len(state.changes) + 1
            state.changes[number] = change(self._repo, number, branch=head, sha=state.heads[head], base=base)
            return state.changes[number]

        return self._double.answer("open_change", self._repo, default, head=head, base=base, title=title, body=body)

    async def change(self, number: int) -> Change:
        def default() -> Change:
            found = self._state.changes.get(number)
            if found is None:
                raise ForgeNotFound(ForgeObject.CHANGE)
            return self._live(found)

        return self._double.answer("change", self._repo, default, number=number)

    async def update_from_base(self, number: int, expected_head: str) -> Update:
        return self._double.answer(
            "update_from_base", self._repo, lambda: self._state.update, number=number, expected_head=expected_head
        )

    async def merge(self, number: int, expected_head: str, method: MergeMethod, message: str) -> str:
        def default() -> str:
            state = self._state
            merge_commit = f"merge-{self._repo}-{number}"
            state.changes[number] = replace(
                state.changes[number],
                state=ChangeState.MERGED,
                merged_revision=merge_commit,
                head_revision=expected_head,
            )
            state.base_tip = merge_commit
            if state.on_merge is not None:
                state.on_merge(number)
            return merge_commit

        return self._double.answer(
            "merge", self._repo, default, number=number, expected_head=expected_head, method=method, message=message
        )

    async def checks(self, ref: str) -> tuple[Check, ...]:
        def default() -> tuple[Check, ...]:
            found = self._state.checks.get(ref, GREEN)
            if found is None:
                raise ForgeUnavailable("server error")
            return found

        return self._double.answer("checks", self._repo, default, ref=ref)

    async def change_checks(self, number: int) -> tuple[Check, ...]:
        def default() -> tuple[Check, ...]:
            found = self._state.changes.get(number)
            if found is None:
                raise ForgeNotFound(ForgeObject.CHANGE)
            return self._state.checks.get(self._live(found).head_revision, GREEN) or ()

        return self._double.answer("change_checks", self._repo, default, number=number)

    async def rerun_check(self, id: str) -> bool:
        return self._double.answer("rerun_check", self._repo, lambda: True, id=id)


def _conforms(x: _Adapter) -> IForgeAdapter:
    return x


def forge_holding(*repos: str, branch: str, commit: str, **state: Any) -> GitHubDouble:
    """A double whose every named repository has ``branch`` pushed at ``commit``, each in ``state``."""
    return GitHubDouble({repo: DoubleRepo(**{"heads": {branch: commit}, **state}) for repo in repos})
