"""Opaque tenant-scoped units of work and their one-shot lock declaration."""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Protocol


class TransactionMisuse(RuntimeError):
    """A transaction was used outside its lifetime or without the required authority."""


class GraduationError(TransactionMisuse):
    """A transaction cannot acquire locks after a statement or a prior graduation."""


class Transaction(Protocol):
    """An open write unit, opaque to domain callers.

    Graduation: a unit declares the resources it locks exactly once — through one ``lock_*``
    call, made before any repository statement runs in it — and receives a
    :class:`LockedTransaction`. A second declaration, or one after a statement, raises
    :class:`GraduationError`; a token used after its unit closes raises
    :class:`TransactionMisuse`. Repositories refuse a chunk or key the unit does not lock.
    """

    def lock_chunks(self, chunk_ids: Sequence[str]) -> "LockedTransaction": ...

    def lock_keys(self, namespace: str, keys: Sequence[str]) -> "LockedTransaction": ...

    def lock_set(self, *, chunks: Sequence[str] = (), keys: Sequence[tuple[str, str]] = ()) -> "LockedTransaction": ...


class LockedTransaction(Transaction, Protocol):
    """A graduated unit; store adapters must additionally check resource coverage.

    It still carries the ``lock_*`` methods of :class:`Transaction`, but a graduated unit cannot
    lock again: every one raises :class:`GraduationError`."""

    @property
    def locked(self) -> bool: ...


class IHubTransactions(Protocol):
    """Opens tenant-scoped units of work."""

    def begin(self) -> AbstractContextManager[Transaction]:
        """Open one write unit that commits when the block exits cleanly and rolls back otherwise."""
        ...
