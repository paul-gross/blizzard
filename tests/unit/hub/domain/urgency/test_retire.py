"""A retire whose locked re-read finds an open chunk outside its locked set, run against fakes of its ports."""

from __future__ import annotations

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.kernel.transactions import IHubTransactions, Transaction
from blizzard.hub.domain.urgency.model import MoveTo, Urgency, UrgencyId
from blizzard.hub.domain.urgency.ports import IReadTenantSettings, IWriteUrgencyChunks, IWriteUrgencyRepository
from blizzard.hub.domain.urgency.services import UrgencyLifecycle
from blizzard.hub.domain.urgency.settings import TenantSettings
from tests.support.urgency import urgency

_AT = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)


_DEFAULT = urgency("standard")
_RETIRING = urgency("hotfix")
_TARGET = urgency("routine")


@dataclass
class _Transactions:
    chunk_sets: list[list[str]] = field(default_factory=list[list[str]])

    @contextmanager
    def begin(self) -> Generator[Transaction]:
        yield cast(Transaction, self)

    def lock_set(self, *, chunks: Sequence[str] = (), keys: Sequence[tuple[str, str]] = ()) -> Transaction:
        self.chunk_sets.append(list(chunks))
        return cast(Transaction, self)


@dataclass
class _Chunks:
    """Reads ``ahead`` outside a unit; under one, ``ahead`` plus ``late`` — a chunk that took the urgency since."""

    ahead: list[str]
    late: list[str]
    moves: list[list[str]] = field(default_factory=list[list[str]])

    def open_chunks(self, urgency_id: UrgencyId, *, transaction: Transaction | None = None) -> list[str]:
        return list(self.ahead) if transaction is None else sorted([*self.ahead, *self.late])

    def move(
        self, chunk_ids: Sequence[str], *, from_id: UrgencyId, to: UrgencyId, transaction: Transaction | None = None
    ) -> list[str]:
        self.moves.append(list(chunk_ids))
        return list(chunk_ids)


@dataclass
class _Urgencies:
    recorded: list[Urgency] = field(default_factory=list[Urgency])

    def get(self, urgency_id: UrgencyId, *, transaction: Transaction | None = None) -> Urgency | None:
        return {u.urgency_id: u for u in (_DEFAULT, _RETIRING, _TARGET)}.get(urgency_id)

    def record_lifecycle(self, urgency: Urgency, **_: Any) -> Urgency:
        self.recorded.append(urgency)
        return urgency


class _Settings:
    def get(self, *, transaction: Transaction | None = None) -> TenantSettings:
        return TenantSettings(
            tenant_id="ten_a", default_urgency_id=_DEFAULT.urgency_id, revision=1, updated_at=_AT, updated_by="usr_op"
        )


def test_a_chunk_found_open_outside_the_locked_set_widens_the_lock_and_retries_writing_nothing_first() -> None:
    """The first pass returns before any write; the second locks the late chunk too and moves both."""
    transactions = _Transactions()
    chunks = _Chunks(ahead=["chk_a"], late=["chk_b"])
    urgencies = _Urgencies()
    lifecycle = UrgencyLifecycle(
        urgencies=cast(IWriteUrgencyRepository, urgencies),
        settings=cast(IReadTenantSettings, _Settings()),
        chunks=cast(IWriteUrgencyChunks, chunks),
        transactions=cast(IHubTransactions, transactions),
        clock=FixedClock(_AT),
    )

    retired = lifecycle.retire(
        _RETIRING,
        ChangeContext(actor="usr_op", door=Door.CLI),
        move_to=MoveTo(key=_TARGET.slug, urgency=_TARGET),
        may_control=True,
        if_match=None,
    )

    assert transactions.chunk_sets == [["chk_a"], ["chk_a", "chk_b"]]
    assert chunks.moves == [["chk_a", "chk_b"]]
    assert len(urgencies.recorded) == 1
    assert retired.moved == ("chk_a", "chk_b")
    assert retired.urgency.retired
