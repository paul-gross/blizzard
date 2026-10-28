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
from collections.abc import Callable, Mapping
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
from blizzard.hub.forge.landing import (
    OPERATION_CAPABILITY,
    Change,
    ChangeListing,
    Check,
    CommitPresence,
    Comparison,
    ForgeCapabilityMissing,
    IForgeChanges,
    IForgeChecks,
    IForgeRefs,
    MergeMethod,
    RepositoryForge,
    Update,
)
from blizzard.hub.live_config import ConfigObjectCache


class IForgeAdapter(IForgeRefs, IForgeChanges, IForgeChecks, Protocol):
    """What a build callable answers: every role at once, closed by the cache when its records move on. Only the
    registry sees one; callers get its roles."""

    def close(self) -> None:
        """Release the adapter's client on the loop it ran on, returning at once."""
        ...


@domain_model
@dataclass(frozen=True)
class ForgeBinding:
    """Everything one repository's adapter is built from. ``write_lock`` answers the lock every write on this forge
    and credential goes out under; it is called on the loop the adapter runs on."""

    kind: str
    api_url: str
    owner: str
    repo: str
    token: str
    write_lock: Callable[[], asyncio.Lock]


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
    ``FORGE_KIND_CAPABILITIES`` in production."""

    def __init__(
        self,
        *,
        records: IReadRepositoryRecordRepository,
        forges: IReadForgeRepository,
        objects: ConfigObjectCache,
        secrets: ISecretReader,
        build: Callable[[ForgeBinding], IForgeAdapter],
        capabilities: Mapping[str, frozenset[Capability]],
    ) -> None:
        self._records = records
        self._forges = forges
        self._objects = objects
        self._secrets = secrets
        self._build = build
        self._capabilities = capabilities
        self._locks = WriteLocks()

    def offered(self, forge: ConfiguredForge, repository: RepositoryId | None = None) -> frozenset[Capability]:
        """What ``forge`` offers, for ``repository`` when one is named; every forge is its kind's set for now."""
        return self._capabilities[forge.kind]

    def refs(self, repository: RepositoryId) -> IForgeRefs:
        return _RefsHandle(self._adapter(repository), self._offered_for(repository))

    def changes(self, repository: RepositoryId) -> IForgeChanges:
        return _ChangesHandle(self._adapter(repository), self._offered_for(repository))

    def checks(self, repository: RepositoryId) -> IForgeChecks:
        return _ChecksHandle(self._adapter(repository), self._offered_for(repository))

    def repository_forge(self, repository: RepositoryId) -> RepositoryForge:
        """The three roles of ``repository``'s forge bundled, as a visit hands them to an action that lands."""
        adapter, offered = self._adapter(repository), self._offered_for(repository)
        return RepositoryForge(
            repository_id=repository,
            refs=_RefsHandle(adapter, offered),
            changes=_ChangesHandle(adapter, offered),
            checks=_ChecksHandle(adapter, offered),
            offered=offered,
        )

    def _offered_for(self, repository: RepositoryId) -> frozenset[Capability]:
        record = self._records.get(repository)
        assert record is not None
        return self.offered(self._forge_of(record.fields.forge_id), repository)

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
            )
        )


def _require(offered: frozenset[Capability], operation: str) -> None:
    capability = OPERATION_CAPABILITY[operation]
    if capability not in offered:
        raise ForgeCapabilityMissing(capability)


@dataclass(frozen=True)
class _RefsHandle:
    adapter: IForgeRefs
    offered: frozenset[Capability]

    async def branch_tip(self, branch: str) -> str | None:
        _require(self.offered, "branch_tip")
        return await self.adapter.branch_tip(branch)

    async def compare(self, base: str, head: str) -> Comparison:
        _require(self.offered, "compare")
        return await self.adapter.compare(base, head)

    async def commit_exists(self, revision: str) -> CommitPresence:
        _require(self.offered, "commit_exists")
        return await self.adapter.commit_exists(revision)

    async def advance_ref(self, branch: str, expected: str, revision: str) -> None:
        _require(self.offered, "advance_ref")
        await self.adapter.advance_ref(branch, expected, revision)

    async def merge_into(self, branch: str, revision: str, message: str) -> str | None:
        _require(self.offered, "merge_into")
        return await self.adapter.merge_into(branch, revision, message)

    async def create_ref(self, branch: str, revision: str) -> None:
        _require(self.offered, "create_ref")
        await self.adapter.create_ref(branch, revision)

    async def delete_ref(self, branch: str) -> None:
        _require(self.offered, "delete_ref")
        await self.adapter.delete_ref(branch)


@dataclass(frozen=True)
class _ChangesHandle:
    adapter: IForgeChanges
    offered: frozenset[Capability]

    async def changes(self, state: ChangeListing, base: str, head: str | None = None) -> tuple[Change, ...]:
        _require(self.offered, "changes")
        return await self.adapter.changes(state, base, head)

    async def recently_closed(self, base: str, limit: int) -> tuple[Change, ...]:
        _require(self.offered, "recently_closed")
        return await self.adapter.recently_closed(base, limit)

    async def open_change(self, head: str, base: str, title: str, body: str) -> Change:
        _require(self.offered, "open_change")
        return await self.adapter.open_change(head, base, title, body)

    async def change(self, number: int) -> Change:
        _require(self.offered, "change")
        return await self.adapter.change(number)

    async def update_from_base(self, number: int, expected_head: str) -> Update:
        _require(self.offered, "update_from_base")
        return await self.adapter.update_from_base(number, expected_head)

    async def merge(self, number: int, expected_head: str, method: MergeMethod, message: str) -> str:
        _require(self.offered, f"merge.{method}")
        return await self.adapter.merge(number, expected_head, method, message)


@dataclass(frozen=True)
class _ChecksHandle:
    adapter: IForgeChecks
    offered: frozenset[Capability]

    async def checks(self, ref: str) -> tuple[Check, ...]:
        _require(self.offered, "checks")
        return await self.adapter.checks(ref)

    async def change_checks(self, number: int) -> tuple[Check, ...]:
        _require(self.offered, "change_checks")
        return await self.adapter.change_checks(number)

    async def rerun_check(self, id: str) -> bool:
        _require(self.offered, "rerun_check")
        return await self.adapter.rerun_check(id)


_refs_conforms: type[IForgeRefs] = _RefsHandle


_changes_conforms: type[IForgeChanges] = _ChangesHandle


_checks_conforms: type[IForgeChecks] = _ChecksHandle
