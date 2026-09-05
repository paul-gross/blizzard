"""View dataclasses shared across two or more hub CLI concept modules."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass(frozen=True)
class Cost:
    """A derived cost total (issue #60) — always to the cent, with a leading ``~`` when
    partial, i.e. a lower bound never presented as exact."""

    cost_usd: float
    partial: bool

    @classmethod
    def of(cls, cost: dict[str, Any] | None) -> Cost:
        cost = cost or {}
        return cls(cost.get("cost_usd", 0.0), cost.get("cost_partial", False))

    @property
    def rendered(self) -> str:
        amount = f"${self.cost_usd:.2f}"
        return f"~{amount}" if self.partial else amount


@dataclass(frozen=True)
class ChunkRow:
    row: dict[str, Any]
    #: ``status`` renders the node id where ``chunk list`` prefers the node's name.
    prefer_node_name: bool = True

    @property
    def node(self) -> str:
        name = self.row.get("current_node_name") if self.prefer_node_name else None
        return name or self.row.get("current_node_id") or "-"

    def line(self) -> str:
        rendered = Cost.of(self.row.get("cost")).rendered
        blocked = self.row.get("blocked")
        marking = f"  [blocked on {blocked['prerequisite_chunk_id']}]" if blocked else ""
        return f"{self.row['chunk_id']}  {self.row['status']:<16} @ {self.node}  {rendered:>10}{marking}"


@dataclass(frozen=True)
class RunnerRow:
    row: dict[str, Any]

    @property
    def liveness(self) -> str:
        return "online" if self.row.get("online") else "offline"

    @property
    def brake(self) -> str:
        """Name which brake is on (issue #43): "paused" alone would hide whether the fleet
        stopped this runner or it stopped itself — they are cleared by different verbs."""
        brakes = []
        if self.row.get("hub_paused"):
            brakes.append("hub")
        if self.row.get("locally_paused"):
            reason = self.row.get("locally_paused_reason")
            brakes.append(f"local — {reason}" if reason else "local")
        return f" [paused: {'+'.join(brakes)}]" if brakes else ""

    def line(self) -> str:
        return f"{self.row['runner_id']:<16} {self.liveness:<8} ws={self.row.get('workspace_id', '-')}{self.brake}"


@dataclass(frozen=True)
class QuestionRow:
    row: dict[str, Any]

    def line(self) -> str:
        options = self.row.get("options") or []
        offered = f"  [{'|'.join(options)}]" if options else ""
        return f"{self.row['question_id']}  (chunk {self.row['chunk_id']}): {self.row['question']}{offered}"


@dataclass(frozen=True)
class Listing:
    rows: Sequence[Any]

    empty: ClassVar[str] = "nothing to show"

    def line(self, row: Any) -> str:
        raise NotImplementedError

    def lines(self) -> Iterator[str]:
        if not self.rows:
            yield self.empty
            return
        for row in self.rows:
            yield self.line(row)
