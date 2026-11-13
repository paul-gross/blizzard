"""An in-memory :class:`IHubTransactions` for a unit test whose domain caller opens its own unit: each unit's
locked chunks recorded in the order the units opened, the token handed on opaque."""

from __future__ import annotations

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import cast

from blizzard.hub.domain.kernel.locks import HubLock
from blizzard.hub.domain.kernel.transactions import IHubTransactions, LockedTransaction, Transaction


@dataclass
class _FakeUnit:
    owner: FakeTransactions

    def lock_chunks(self, chunk_ids: Sequence[str]) -> LockedTransaction:
        return self.lock_set(chunks=chunk_ids)

    def lock_keys(self, namespace: str, keys: Sequence[str]) -> LockedTransaction:
        return self.lock_set(keys=[(namespace, key) for key in keys])

    def lock_set(
        self, *, chunks: Sequence[str] = (), keys: Sequence[tuple[str, str]] = (), hub: Sequence[HubLock] = ()
    ) -> LockedTransaction:
        self.owner.locked_chunks.append(list(chunks))
        return cast(LockedTransaction, self)

    @property
    def locked(self) -> bool:
        return True


@dataclass
class FakeTransactions:
    """Opens a fresh unit per :meth:`begin`; ``locked_chunks`` holds each unit's declared chunks."""

    locked_chunks: list[list[str]] = field(default_factory=list[list[str]])

    @contextmanager
    def begin(self) -> Generator[Transaction]:
        yield _FakeUnit(self)

    def as_port(self) -> IHubTransactions:
        return self
