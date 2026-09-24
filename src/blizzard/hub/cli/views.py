"""View dataclasses shared across two or more hub CLI concept modules."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass(frozen=True)
class Cost:
    """The one CLI cost formatter: one amount, ``cost_usd + (estimated_cost_usd ?? 0)``, to
    the cent, with two independent markers — a leading ``~`` when an estimate is present
    (even ``0.0``) and a trailing ``+`` when partial, a lower bound. The renderings are pinned
    by tests/test_hub_cli_views.py; docs/deployment/spend.md owns the vocabulary."""

    cost_usd: float
    estimated_cost_usd: float | None
    partial: bool

    @classmethod
    def of(cls, cost: dict[str, Any] | None) -> Cost:
        cost = cost or {}
        return cls(
            cost.get("cost_usd", 0.0),
            cost.get("estimated_cost_usd"),
            cost.get("cost_partial", False),
        )

    @property
    def rendered(self) -> str:
        estimated = self.estimated_cost_usd is not None
        amount = self.cost_usd + (self.estimated_cost_usd or 0.0)
        prefix = "~" if estimated else ""
        suffix = "+" if self.partial else ""
        return f"{prefix}${amount:.2f}{suffix}"


@dataclass(frozen=True)
class ChunkRow:
    row: dict[str, Any]
    #: True renders the node's name when known; false renders its id.
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
        """Name which brake is on (issue #43; pinned by
        tests/test_hub_cli_status.py::test_status_names_a_hub_pause_with_no_local_brake)."""
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
