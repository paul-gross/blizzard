"""The forge registry — each repository's forge roles, built from its records on use.

Every hand-out reads the repository's revisions — its own, its credential's, its secret's and its forge's — and takes
the adapter built under them, beside the authorization it was built from, from the tenant's
:class:`~blizzard.hub.live_config.ConfigObjectCache`, so an edit to any of them reaches the next use with no restart
(``bzh:config-read-on-use``). Only whoever builds a visit calls it, never an action; each role it hands out checks
every call against what the forge offers."""

from __future__ import annotations

import asyncio

# Guards this process's own lock map, never a store write.
# ast-grep-ignore: bzh:store-exclusive-write
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Protocol

from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.changes import RecordKind
from blizzard.hub.domain.config.credentials import IReadCredentialRepository, resolve_credential
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeId, IReadForgeRepository
from blizzard.hub.domain.config.repositories import IReadRepositoryRecordRepository, RepositoryId
from blizzard.hub.forge.auth import ForgeAuthorization, ForgeScope, IForgeAuthorizations
from blizzard.hub.forge.budget import (
    BudgetKey,
    BudgetReading,
    ForgeBudgetLow,
    ForgeBudgets,
    ForgePriority,
    IForgeRateDialect,
)
from blizzard.hub.forge.landing import (
    OPERATION_CAPABILITY,
    Change,
    ChangeListing,
    Check,
    CommitPresence,
    Comparison,
    ForgeCapabilityMissing,
    ForgeRateLimited,
    IForgeChanges,
    IForgeChecks,
    IForgeRefs,
    MergeMethod,
    RepositoryForge,
    Rerun,
    Update,
)
from blizzard.hub.live_config import ConfigObjectCache


class IForgeAdapter(IForgeRefs, IForgeChanges, IForgeChecks, Protocol):
    """What a build callable answers: every role at once, released by :meth:`close`."""

    def close(self) -> None:
        """Release the adapter's client on the loop it ran on, returning at once."""
        ...


@dataclass(frozen=True)
class ForgeBinding:
    """Everything one repository's adapter is built from. ``authorization`` authenticates its every request.
    ``write_lock`` answers the lock every write on this forge as this authority goes out under; it is called on the
    loop the adapter runs on. ``dialect`` is the forge kind's way of reading its answers' rate facts, and ``learn``
    takes every reading into the forge and authority's rate budget."""

    kind: str
    api_url: str
    owner: str
    repo: str
    authorization: ForgeAuthorization
    write_lock: Callable[[], asyncio.Lock]
    dialect: IForgeRateDialect
    learn: Callable[[BudgetReading], None]


class WriteLocks:
    """One write lock per (forge id, authority), each created on the first call for its pair — made from the action
    loop, so the lock belongs to the loop every adapter runs on."""

    def __init__(self) -> None:
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._guard = threading.Lock()

    def lock(self, forge: ForgeId, authority: str) -> asyncio.Lock:
        with self._guard:
            return self._locks.setdefault((forge, authority), asyncio.Lock())


@dataclass(frozen=True)
class _Built:
    """One repository's adapter, the authorization it was built from, and the budget key its calls spend, cached and
    released as one."""

    adapter: IForgeAdapter
    authorization: ForgeAuthorization
    key: BudgetKey

    def close(self) -> None:
        self.adapter.close()


class ForgeRegistry:
    """``build`` turns a binding into its adapter; ``capabilities`` names what each forge kind offers —
    ``FORGE_KIND_CAPABILITIES`` in production. ``authorizations`` builds the authorization of the credential each
    repository acts with, resolved through ``credentials``. ``budgets`` is the tenant's, held apart from the adapters
    so a rebuilt adapter spends the budget its predecessor did; every role it hands out is admitted against it at its
    caller's priority."""

    def __init__(
        self,
        *,
        records: IReadRepositoryRecordRepository,
        forges: IReadForgeRepository,
        credentials: IReadCredentialRepository,
        objects: ConfigObjectCache,
        authorizations: IForgeAuthorizations,
        build: Callable[[ForgeBinding], IForgeAdapter],
        capabilities: Mapping[str, frozenset[Capability]],
        budgets: ForgeBudgets,
        write_locks: WriteLocks,
        dialects: Callable[[str], IForgeRateDialect],
    ) -> None:
        self._records = records
        self._forges = forges
        self._credentials = credentials
        self._objects = objects
        self._authorizations = authorizations
        self._build = build
        self._capabilities = capabilities
        self._locks = write_locks
        self._dialects = dialects
        self._budgets = budgets

    @property
    def authorizations(self) -> IForgeAuthorizations:
        return self._authorizations

    def offered(self, forge: ConfiguredForge, repository: RepositoryId | None = None) -> frozenset[Capability]:
        """What ``forge`` offers: its kind's set, narrowed for ``repository`` when one is named to what the
        authorization it acts with can do there. :class:`~blizzard.hub.domain.config.credentials.CredentialUnavailable`
        when that repository's credential or secret is unknown or retired."""
        kind = self._capabilities[forge.kind]
        if repository is None:
            return kind
        return kind & self._built(repository).authorization.offered

    def authorization(self, repository: RepositoryId) -> ForgeAuthorization:
        """The authorization ``repository`` acts with, as its adapter was built from;
        :class:`~blizzard.hub.domain.config.credentials.CredentialUnavailable` as :meth:`offered` raises it."""
        return self._built(repository).authorization

    def refs(self, repository: RepositoryId, *, priority: ForgePriority) -> IForgeRefs:
        built = self._built(repository)
        return RefsHandle(built.adapter, self._offered_for(repository), self._gate(built, priority))

    def changes(self, repository: RepositoryId, *, priority: ForgePriority) -> IForgeChanges:
        built = self._built(repository)
        return _ChangesHandle(built.adapter, self._offered_for(repository), self._gate(built, priority))

    def checks(self, repository: RepositoryId, *, priority: ForgePriority) -> IForgeChecks:
        built = self._built(repository)
        return _ChecksHandle(built.adapter, self._offered_for(repository), self._gate(built, priority))

    def repository_forge(
        self, repository: RepositoryId, *, cut_off: TurnCutOff, priority: ForgePriority
    ) -> RepositoryForge:
        """The three roles of ``repository``'s forge bundled, as a turn hands them to an action that lands, each
        refusing once ``cut_off`` is set."""
        built, offered = self._built(repository), self._offered_for(repository)
        adapter, gate = built.adapter, self._gate(built, priority)
        return RepositoryForge(
            repository_id=repository,
            refs=RefsHandle(adapter, offered, gate, cut_off),
            changes=_ChangesHandle(adapter, offered, gate, cut_off),
            checks=_ChecksHandle(adapter, offered, gate, cut_off),
            offered=offered,
        )

    def _offered_for(self, repository: RepositoryId) -> frozenset[Capability]:
        record = self._records.get(repository)
        assert record is not None
        return self.offered(self._forge_of(record.fields.forge_id), repository)

    def _gate(self, built: _Built, priority: ForgePriority) -> BudgetGate:
        return BudgetGate(self._budgets, built.key, priority)

    def _forge_of(self, forge_id: ForgeId) -> ConfiguredForge:
        forge = self._forges.get(forge_id)
        # Every repository names a forge, and a configured record is never deleted.
        assert forge is not None
        return forge

    def _built(self, repository: RepositoryId) -> _Built:
        """The adapter built under the repository's current revisions — a retired record's too, so a chunk that named
        it still lands. :class:`~blizzard.hub.domain.config.credentials.CredentialUnavailable` when its credential or
        secret is unknown or retired."""
        built = self._objects.get(RecordKind.REPOSITORY, repository, partial(self._build_current, repository))
        # The executor resolved the repository from the records, and a configured record is never deleted.
        assert built is not None
        return built

    def _build_current(self, repository: RepositoryId) -> _Built:
        record = self._records.get(repository)
        assert record is not None
        fields = record.fields
        forge = self._forge_of(fields.forge_id)
        credential = resolve_credential(record, self._credentials)
        # A repository always names a secret, so it always resolves a credential.
        assert credential is not None
        authorization = self._authorizations.authorization(credential, ForgeScope.LANDING)
        key = BudgetKey(forge.forge_id, authorization.authority)
        adapter = self._build(
            ForgeBinding(
                kind=forge.kind,
                api_url=forge.fields.api_base,
                owner=fields.owner,
                repo=fields.repo,
                authorization=authorization,
                write_lock=partial(self._locks.lock, forge.forge_id, authorization.authority),
                dialect=self._dialects(forge.kind),
                learn=partial(self._budgets.learn, key),
            )
        )
        return _Built(adapter, authorization, key)


class TurnCutOff:
    """A turn's flag its forge handles check before every call: set once the turn is cut off, it refuses each call
    the pass makes afterwards before anything reaches the forge. One per turn, never on a cached adapter."""

    def __init__(self) -> None:
        self._set = False

    def set(self) -> None:
        self._set = True

    def is_set(self) -> bool:
        return self._set


class ForgeCallRefused(Exception):
    """A forge call refused before it was sent: the turn making it was cut off."""


def _require(offered: frozenset[Capability], operation: str, cut_off: TurnCutOff | None = None) -> None:
    if cut_off is not None and cut_off.is_set():
        raise ForgeCallRefused(f"`{operation}` refused: the turn was cut off")
    capability = OPERATION_CAPABILITY[operation]
    if capability not in offered:
        raise ForgeCapabilityMissing(capability)


@dataclass(frozen=True)
class BudgetGate:
    """One handle's way into its forge key's budget, at its caller's priority: admitted once per seam call before the
    call is made, and marked spent when the forge refuses the call for its rate."""

    budgets: ForgeBudgets
    key: BudgetKey
    priority: ForgePriority

    def admit(self) -> None:
        self.budgets.admit(self.key, self.priority)

    async def spending[T](self, call: Awaitable[T]) -> T:
        try:
            return await call
        except ForgeBudgetLow:
            raise
        except ForgeRateLimited as exc:
            self.budgets.spent(self.key, exc.resume_at)
            raise


def _admit(offered: frozenset[Capability], operation: str, gate: BudgetGate, cut_off: TurnCutOff | None) -> None:
    _require(offered, operation, cut_off)
    gate.admit()


@dataclass(frozen=True)
class RefsHandle:
    adapter: IForgeRefs
    offered: frozenset[Capability]
    gate: BudgetGate
    cut_off: TurnCutOff | None = None

    async def branch_tip(self, branch: str) -> str | None:
        _admit(self.offered, "branch_tip", self.gate, self.cut_off)
        return await self.gate.spending(self.adapter.branch_tip(branch))

    async def compare(self, base: str, head: str) -> Comparison:
        _admit(self.offered, "compare", self.gate, self.cut_off)
        return await self.gate.spending(self.adapter.compare(base, head))

    async def commit_exists(self, revision: str) -> CommitPresence:
        _admit(self.offered, "commit_exists", self.gate, self.cut_off)
        return await self.gate.spending(self.adapter.commit_exists(revision))

    async def advance_ref(self, branch: str, expected: str, revision: str) -> None:
        _admit(self.offered, "advance_ref", self.gate, self.cut_off)
        await self.gate.spending(self.adapter.advance_ref(branch, expected, revision))

    async def merge_into(self, branch: str, revision: str, message: str) -> str | None:
        _admit(self.offered, "merge_into", self.gate, self.cut_off)
        return await self.gate.spending(self.adapter.merge_into(branch, revision, message))

    async def create_ref(self, branch: str, revision: str) -> None:
        _admit(self.offered, "create_ref", self.gate, self.cut_off)
        await self.gate.spending(self.adapter.create_ref(branch, revision))

    async def delete_ref(self, branch: str) -> None:
        _admit(self.offered, "delete_ref", self.gate, self.cut_off)
        await self.gate.spending(self.adapter.delete_ref(branch))


@dataclass(frozen=True)
class _ChangesHandle:
    adapter: IForgeChanges
    offered: frozenset[Capability]
    gate: BudgetGate
    cut_off: TurnCutOff | None = None

    async def changes(self, state: ChangeListing, base: str, head: str | None = None) -> tuple[Change, ...]:
        _admit(self.offered, "changes", self.gate, self.cut_off)
        return await self.gate.spending(self.adapter.changes(state, base, head))

    async def recently_closed(self, base: str, limit: int) -> tuple[Change, ...]:
        _admit(self.offered, "recently_closed", self.gate, self.cut_off)
        return await self.gate.spending(self.adapter.recently_closed(base, limit))

    async def open_change(self, head: str, base: str, title: str, body: str) -> Change:
        _admit(self.offered, "open_change", self.gate, self.cut_off)
        return await self.gate.spending(self.adapter.open_change(head, base, title, body))

    async def change(self, number: int) -> Change:
        _admit(self.offered, "change", self.gate, self.cut_off)
        return await self.gate.spending(self.adapter.change(number))

    async def update_from_base(self, number: int, expected_head: str) -> Update:
        _admit(self.offered, "update_from_base", self.gate, self.cut_off)
        return await self.gate.spending(self.adapter.update_from_base(number, expected_head))

    async def merge(self, number: int, expected_head: str, method: MergeMethod, message: str) -> str:
        _admit(self.offered, f"merge.{method}", self.gate, self.cut_off)
        return await self.gate.spending(self.adapter.merge(number, expected_head, method, message))


@dataclass(frozen=True)
class _ChecksHandle:
    adapter: IForgeChecks
    offered: frozenset[Capability]
    gate: BudgetGate
    cut_off: TurnCutOff | None = None

    async def checks(self, ref: str) -> tuple[Check, ...]:
        _admit(self.offered, "checks", self.gate, self.cut_off)
        return await self.gate.spending(self.adapter.checks(ref))

    async def change_checks(self, number: int) -> tuple[Check, ...]:
        _admit(self.offered, "change_checks", self.gate, self.cut_off)
        return await self.gate.spending(self.adapter.change_checks(number))

    async def rerun_check(self, id: str) -> Rerun:
        _admit(self.offered, "rerun_check", self.gate, self.cut_off)
        return await self.gate.spending(self.adapter.rerun_check(id))


_refs_conforms: type[IForgeRefs] = RefsHandle


_changes_conforms: type[IForgeChanges] = _ChangesHandle


_checks_conforms: type[IForgeChecks] = _ChecksHandle
