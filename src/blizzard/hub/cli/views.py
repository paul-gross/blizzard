"""View dataclasses shared across two or more hub CLI concept modules."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, cast

from blizzard.foundation.roles import collaborator, dto
from blizzard.foundation.runner_connection import RunnerConnection
from blizzard.foundation.spend import SpendFigure


def pointer_label(row: dict[str, object]) -> str:
    """A pointer a response carries, as people read it: its hub-rendered ``label``, else ``{slug}#{ref}`` under the
    source's current slug, else the raw ``{source}#{ref}``."""
    return cast(str, row.get("label")) or f"{row.get('slug') or row.get('source')}#{row.get('ref')}"


@dto
@dataclass(frozen=True)
class Cost:
    """A response's ``cost`` object read as the one spend figure (:class:`~blizzard.foundation.spend.SpendFigure`),
    which owns its rendering; renderings pinned by tests/test_hub_cli_views.py."""

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
        return SpendFigure(self.cost_usd, self.estimated_cost_usd, self.partial).rendered()


@dto
@dataclass(frozen=True)
class ChunkLine:
    row: dict[str, Any]
    #: True renders the node's name when known; false renders its id.
    prefer_node_name: bool = True

    @property
    def node(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        name = self.row.get("current_node_name") if self.prefer_node_name else None
        return name or self.row.get("current_node_id") or "-"

    def line(self) -> str:
        rendered = Cost.of(self.row.get("cost")).rendered
        blocked = self.row.get("blocked")
        marking = f"  [blocked on {blocked['prerequisite_chunk_id']}]" if blocked else ""
        return f"{self.row['chunk_id']}  {self.row['status']:<16} @ {self.node}  {rendered:>10}{marking}"


def _declining_reason(reason: dict[str, Any]) -> str:
    """One declining reason as a line carries it: the floor as ``floor — <subscription> <detail>, resets <instant>``,
    any other lever, and a lever this CLI has no wording for, as ``<lever> — <detail>`` with its ``until`` after."""
    until = reason.get("until")
    clock = datetime.fromisoformat(until).strftime("%H:%MZ") if until else None
    lever, detail = reason["lever"], reason["detail"]
    if lever == "floor":
        named = f"{reason['subscription']} {detail}" if reason.get("subscription") else detail
        return f"floor — {named}" + (f", resets {clock}" if clock else "")
    return f"{lever} — {detail}" + (f", until {clock}" if clock else "")


@dto
@dataclass(frozen=True)
class RunnerLine:
    row: dict[str, Any]
    #: The name column's width; a listing widens it to the longest name it shows.
    name_width: int = 16

    #: The name column's narrowest width.
    NAME_WIDTH: ClassVar[int] = 16

    @property
    def name(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        """The runner's display name — not unique, so always rendered beside its id."""
        return self.row.get("runner_name") or "-"

    @property
    def connection(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        """``never-connected`` for a runner added but never registered — never ``offline``,
        which it has not yet been — else ``online`` or ``offline``."""
        if self.row.get("connection") == RunnerConnection.NEVER_CONNECTED:
            return "never-connected"
        return "online" if self.row.get("online") else "offline"

    @property
    def workspace(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        """The workspace the runner registered from; ``-`` before it first registers."""
        return self.row.get("workspace_id") or "-"

    @property
    def brake(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        """Name which brake is on (pinned by
        tests/test_hub_cli_status.py::test_status_names_a_hub_pause_with_no_local_brake)."""
        brakes: list[str] = []
        if self.row.get("hub_paused"):
            brakes.append("hub")
        if self.row.get("locally_paused"):
            reason = self.row.get("locally_paused_reason")
            brakes.append(f"local — {reason}" if reason else "local")
        return f" [paused: {'+'.join(brakes)}]" if brakes else ""

    @property
    def declining(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        """The first throttle an online, unbraked runner reported as saying no — the brake wins the slot; nothing for an
        offline runner or one that reported none."""
        reasons = cast(list[dict[str, Any]], self.row.get("declining") or [])
        if not reasons or self.brake or self.connection != "online":
            return ""
        return f" [declining: {_declining_reason(reasons[0])}]"

    @property
    def retirement(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        """When and by whom a retired runner was retired; empty for a live one."""
        if not self.row.get("retired"):
            return ""
        return f" [retired {self.row.get('retired_at') or '-'} by {self.row.get('retired_by') or '-'}]"

    def line(self) -> str:
        return (
            f"{self.row['runner_id']}  {self.name:<{self.name_width}} {self.connection:<15} ws={self.workspace}"
            f"{self.brake}{self.declining}{self.retirement}"
        )


@dto
@dataclass(frozen=True)
class QuestionLine:
    row: dict[str, Any]

    def line(self) -> str:
        options = cast(list[str], self.row.get("options") or [])
        offered = f"  [{'|'.join(options)}]" if options else ""
        return f"{self.row['question_id']}  (chunk {self.row['chunk_id']}): {self.row['question']}{offered}"


@collaborator
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


@dto
@dataclass(frozen=True)
class ProposalOrigin:
    """A garden proposal's origin tokens: `origin=` always, `routine=` when named,
    `created_by=` for an operator row that carries one (a counts row does not)."""

    row: dict[str, Any]

    @property
    def rendered(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        origin = self.row["origin"]
        tokens = [f"origin={origin}"]
        if self.row.get("routine_name") is not None:
            tokens.append(f"routine={self.row['routine_name']}")
        if origin == "operator" and "created_by" in self.row:
            tokens.append(f"created_by={self.row['created_by']}")
        return "  ".join(tokens)
