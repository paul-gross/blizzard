"""Opaque tenant-scoped units of work and their one-shot lock declaration."""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Protocol


class TransactionMisuse(RuntimeError):
    """A transaction was used outside its lifetime or without the required authority."""


class GraduationError(TransactionMisuse):
    """A transaction cannot acquire locks after a statement or a prior graduation."""


class Transaction(Protocol):
    """An open write unit, opaque to domain callers."""

    def lock_chunks(self, chunk_ids: Sequence[str]) -> "LockedTransaction": ...

    def lock_keys(self, namespace: str, keys: Sequence[str]) -> "LockedTransaction": ...

    def lock_set(self, *, chunks: Sequence[str] = (), keys: Sequence[tuple[str, str]] = ()) -> "LockedTransaction": ...


class LockedTransaction(Transaction, Protocol):
    """A graduated unit; store adapters must additionally check resource coverage."""

    @property
    def locked(self) -> bool: ...


class IHubTransactions(Protocol):
    def begin(self) -> AbstractContextManager[Transaction]: ...
