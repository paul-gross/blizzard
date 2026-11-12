"""An in-memory status-facts reader for a unit test whose domain caller derives a fenced write's terminal verdict
(``blizzard.hub.domain.chunk.terminal``): ``facts`` keyed by chunk id, an absent id read as an unknown chunk, and
each read's ``transaction`` recorded in ``transactions``."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from blizzard.hub.domain.chunk.model import ChunkFacts
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.kernel.transactions import Transaction


@dataclass
class FakeStatusFacts:
    facts: dict[str, ChunkFacts] = field(default_factory=dict[str, ChunkFacts])
    transactions: list[Transaction | None] = field(default_factory=list[Transaction | None])

    def status_facts_for(
        self, chunk_ids: Sequence[str], *, transaction: Transaction | None = None
    ) -> dict[str, ChunkFacts]:
        self.transactions.append(transaction)
        return {chunk_id: self.facts[chunk_id] for chunk_id in chunk_ids if chunk_id in self.facts}

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")

    def as_port(self) -> IReadChunkFactsRepository:
        return cast(IReadChunkFactsRepository, self)
