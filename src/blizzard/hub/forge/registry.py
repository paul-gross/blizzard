"""The forge registry — each repository's forge roles, built from its records on use.

Every hand-out reads the repository's revisions — its own, its secret's and its forge's — and takes the adapter built
under them from the tenant's :class:`~blizzard.hub.live_config.ConfigObjectCache`, so an edit to any of the three
reaches the next use with no restart (``bzh:config-read-on-use``). Only whoever builds a visit calls it, never an
action; each role it hands out checks every call against what the forge offers."""

from __future__ import annotations

import asyncio

# Guards this process's own lock map, never a store write.
# ast-grep-ignore: bzh:store-exclusive-write
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.changes import RecordKind
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeId, IReadForgeRepository
from blizzard.hub.domain.config.repositories import (
    IReadRepositoryRecordRepository,
    RepositoryId,
    RepositorySecretUnavailable,
)
from blizzard.hub.domain.config.secrets import ISecretReader, SecretName, SecretNotFound, SecretRetired
from blizzard.hub.forge.budget import BudgetKey, ForgeBudgetLow, ForgeBudgets, ForgePriority
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


@domain_model
@dataclass(frozen=True)
class ForgeBinding:
    """Everything one repository's adapter is built from. ``write_lock`` answers the lock every write on this forge
    and credential goes out under; it is called on the loop the adapter runs on. ``learn`` takes every answer's headers
    into the forge and credential's rate budget."""

    kind: str
    api_url: str
    owner: str
    repo: str
    token: str
    write_lock: Callable[[], asyncio.Lock]
    learn: Callable[[Mapping[str, str]], None]


class WriteLocks:
    """One write lock per (forge id, secret name), each created on the first call for its pair — made from the
    action loop, so the lock belongs to the loop every adapter runs on."""

    def __init__(self) -> None:
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._guard = threading.Lock()

    def lock(self, forge: ForgeId, secret: str) -> asyncio.Lock:
        with self._guard:
            return self._locks.setdefault((forge, secret), asyncio.Lock())


class ForgeRegistry:
    """``build`` turns a binding into its adapter; ``capabilities`` names what each forge kind offers —
    ``FORGE_KIND_CAPABILITIES`` in production. ``budgets`` is the tenant's, held apart from the adapters so a rebuilt
    adapter spends the budget its predecessor did; every role it hands out is admitted against it at its caller's
    priority."""

    def __init__(
        self,
        *,
        records: IReadRepositoryRecordRepository,
        forges: IReadForgeRepository,
        objects: ConfigObjectCache,
        secrets: ISecretReader,
        build: Callable[[ForgeBinding], IForgeAdapter],
        capabilities: Mapping[str, frozenset[Capability]],
        budgets: ForgeBudgets,
    ) -> None:
        self._records = records
        self._forges = forges
        self._objects = objects
        self._secrets = secrets
        self._build = build
        self._capabilities = capabilities
        self._locks = WriteLocks()
        self._budgets = budgets

    def offered(self, forge: ConfiguredForge, repository: RepositoryId | None = None) -> frozenset[Capability]:
        """What ``forge`` offers, for ``repository`` when one is named; every forge is its kind's set for now."""
        return self._capabilities[forge.kind]

    def refs(self, repository: RepositoryId, *, priority: ForgePriority) -> IForgeRefs:
        return _RefsHandle(self._adapter(repository), self._offered_for(repository), self._gate(repository, priority))

    def changes(self, repository: RepositoryId, *, priority: ForgePriority) -> IForgeChanges:
        return _ChangesHandle(
            self._adapter(repository), self._offered_for(repository), self._gate(repository, priority)
        )

    def checks(self, repository: RepositoryId, *, priority: ForgePriority) -> IForgeChecks:
        return _ChecksHandle(self._adapter(repository), self._offered_for(repository), self._gate(repository, priority))

    def repository_forge(
        self, repository: RepositoryId, *, cut_off: TurnCutOff, priority: ForgePriority
    ) -> RepositoryForge:
        """The three roles of ``repository``'s forge bundled, as a turn hands them to an action that lands, each
        refusing once ``cut_off`` is set."""
        adapter, offered = self._adapter(repository), self._offered_for(repository)
        gate = self._gate(repository, priority)
        return RepositoryForge(
            repository_id=repository,
            refs=_RefsHandle(adapter, offered, gate, cut_off),
            changes=_ChangesHandle(adapter, offered, gate, cut_off),
            checks=_ChecksHandle(adapter, offered, gate, cut_off),
            offered=offered,
        )

    def _offered_for(self, repository: RepositoryId) -> frozenset[Capability]:
        record = self._records.get(repository)
        assert record is not None
        return self.offered(self._forge_of(record.fields.forge_id), repository)

    def _gate(self, repository: RepositoryId, priority: ForgePriority) -> _BudgetGate:
        record = self._records.get(repository)
        assert record is not None
        return _BudgetGate(self._budgets, BudgetKey(record.fields.forge_id, record.fields.secret_name), priority)

    def _forge_of(self, forge_id: ForgeId) -> ConfiguredForge:
        forge = self._forges.get(forge_id)
        # Every repository names a forge, and a configured record is never deleted.
        assert forge is not None
        return forge

    def _adapter(self, repository: RepositoryId) -> IForgeAdapter:
        """The adapter built under the repository's current revisions — a retired record's too, so a chunk that named
        it still lands. :class:`RepositorySecretUnavailable` when its secret cannot be revealed."""
        adapter = self._objects.get(RecordKind.REPOSITORY, repository, partial(self._build_current, repository))
        # The executor resolved the repository from the records, and a configured record is never deleted.
        assert adapter is not None
        return adapter

    def _build_current(self, repository: RepositoryId) -> IForgeAdapter:
        record = self._records.get(repository)
        assert record is not None
        fields = record.fields
        forge = self._forge_of(fields.forge_id)
        try:
            token = self._secrets.reveal(SecretName.parse(fields.secret_name)).expose()
        except SecretNotFound as exc:
            raise RepositorySecretUnavailable(fields.secret_name, retired=False) from exc
        except SecretRetired as exc:
            raise RepositorySecretUnavailable(fields.secret_name, retired=True) from exc
        return self._build(
            ForgeBinding(
                kind=forge.kind,
                api_url=forge.fields.api_base,
                owner=fields.owner,
                repo=fields.repo,
                token=token,
                write_lock=partial(self._locks.lock, forge.forge_id, fields.secret_name),
                learn=partial(self._budgets.learn, BudgetKey(forge.forge_id, fields.secret_name)),
            )
        )


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
class _BudgetGate:
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


def _admit(offered: frozenset[Capability], operation: str, gate: _BudgetGate, cut_off: TurnCutOff | None) -> None:
    _require(offered, operation, cut_off)
    gate.admit()


@dataclass(frozen=True)
class _RefsHandle:
    adapter: IForgeRefs
    offered: frozenset[Capability]
    gate: _BudgetGate
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
    gate: _BudgetGate
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
    gate: _BudgetGate
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


_refs_conforms: type[IForgeRefs] = _RefsHandle


_changes_conforms: type[IForgeChanges] = _ChangesHandle


_checks_conforms: type[IForgeChecks] = _ChecksHandle
