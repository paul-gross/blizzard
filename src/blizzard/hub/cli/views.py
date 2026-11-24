"""View dataclasses shared across two or more hub CLI concept modules."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, cast

from blizzard.foundation.declared_lever import DeclaredLever
from blizzard.foundation.garden_proposals import GardenProposalOrigin
from blizzard.foundation.roles import collaborator, dto
from blizzard.foundation.runner_connection import RunnerConnection
from blizzard.foundation.spend import SpendFigure
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.chunk import ChunkDetail, ChunkSummary, ChunkUsageTotalView, WorkItemEntry, WorkRefView
from blizzard.wire.fleet import FleetSpendView
from blizzard.wire.question import QuestionView
from blizzard.wire.runner import RunnerDecliningView, RunnerRegistryView
from blizzard.wire.urgency import UrgencyRef


def urgency_label(urgency: UrgencyRef | None) -> str:
    """The urgency a chunk view names, by slug; ``-`` from a hub that names none."""
    return "-" if urgency is None else urgency.slug


def pointer_label(row: WorkRefView | WorkItemEntry | dict[str, object]) -> str:
    """A pointer a response carries, as people read it: its hub-rendered ``label``, else ``{slug}#{ref}`` under the
    source's current slug, else the raw ``{source}#{ref}``."""
    if isinstance(row, WorkRefView):
        return row.label or f"{row.source}#{row.ref}"
    if isinstance(row, WorkItemEntry):
        return row.label or f"{row.slug or row.source}#{row.ref}"
    return cast(str, row.get("label")) or f"{row.get('slug') or row.get('source')}#{row.get('ref')}"


@dto
@dataclass(frozen=True)
class Cost:
    """A response's ``cost`` object read as the one spend figure (:class:`~blizzard.foundation.spend.SpendFigure`),
    which owns its rendering; renderings pinned by tests/unit/hub/cli/test_views.py."""

    cost_usd: float
    estimated_cost_usd: float | None
    partial: bool

    @classmethod
    def of(cls, cost: ChunkUsageTotalView | FleetSpendView | dict[str, Any] | None) -> Cost:
        if isinstance(cost, ChunkUsageTotalView | FleetSpendView):
            return cls(cost.cost_usd, cost.estimated_cost_usd, cost.cost_partial)
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
    row: ChunkSummary | ChunkDetail
    #: True renders the node's name when known; false renders its id.
    prefer_node_name: bool = True
    #: True renders the chunk's urgency column after its status.
    show_urgency: bool = False

    @property
    def node(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        name = self.row.current_node_name if self.prefer_node_name else None
        return name or self.row.current_node_id or "-"

    def line(self) -> str:
        rendered = Cost.of(self.row.cost).rendered
        blocked = self.row.blocked
        marking = f"  [blocked on {blocked.prerequisite_chunk_id}]" if blocked else ""
        urgency = f"{urgency_label(self.row.urgency):<12} " if self.show_urgency else ""
        return f"{self.row.chunk_id}  {self.row.status!s:<16} {urgency}@ {self.node}  {rendered:>10}{marking}"


def _declining_reason(reason: RunnerDecliningView) -> str:
    """One declining reason as a line carries it: the floor as ``floor — <subscription> <detail>, resets <instant>``,
    any other lever, and a lever this CLI has no wording for, as ``<lever> — <detail>`` with its ``until`` after."""
    clock = datetime.fromisoformat(reason.until).strftime("%H:%MZ") if reason.until else None
    lever, detail = str(reason.lever_kind), reason.detail
    if reason.lever_kind == DeclaredLever.FLOOR:
        named = f"{reason.subscription} {detail}" if reason.subscription else detail
        return f"floor — {named}" + (f", resets {clock}" if clock else "")
    return f"{lever} — {detail}" + (f", until {clock}" if clock else "")


@dto
@dataclass(frozen=True)
class RunnerLine:
    row: RunnerRegistryView
    #: The name column's width; a listing widens it to the longest name it shows.
    name_width: int = 16

    #: The name column's narrowest width.
    NAME_WIDTH: ClassVar[int] = 16

    @property
    def name(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        """The runner's display name — not unique, so always rendered beside its id."""
        return self.row.runner_name or "-"

    @property
    def connection(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        """``never-connected`` for a runner added but never registered — never ``offline``,
        which it has not yet been — else ``online`` or ``offline``."""
        if self.row.connection == RunnerConnection.NEVER_CONNECTED:
            return "never-connected"
        return "online" if self.row.online else "offline"

    @property
    def workspace(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        """The workspace the runner registered from; ``-`` before it first registers."""
        return self.row.workspace_id or "-"

    @property
    def brake(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        """Name which brake is on (pinned by
        tests/component/hub/cli/test_status.py::test_status_names_a_hub_pause_with_no_local_brake)."""
        brakes: list[str] = []
        if self.row.hub_paused:
            brakes.append("hub")
        if self.row.locally_paused:
            reason = self.row.locally_paused_reason
            brakes.append(f"local — {reason}" if reason else "local")
        return f" [paused: {'+'.join(brakes)}]" if brakes else ""

    @property
    def declining(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        """The first throttle an online, unbraked runner reported as saying no — the brake wins the slot; nothing for an
        offline runner or one that reported none."""
        reasons = self.row.declining
        if not reasons or self.brake or self.connection != "online":
            return ""
        return f" [declining: {_declining_reason(reasons[0])}]"

    @property
    def retirement(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        """When and by whom a retired runner was retired; empty for a live one."""
        if not self.row.retired:
            return ""
        return f" [retired {self.row.retired_at or '-'} by {self.row.retired_by or '-'}]"

    def line(self) -> str:
        return (
            f"{self.row.runner_id}  {self.name:<{self.name_width}} {self.connection:<15} ws={self.workspace}"
            f"{self.brake}{self.declining}{self.retirement}"
        )


@dto
@dataclass(frozen=True)
class QuestionLine:
    row: QuestionView

    def line(self) -> str:
        options = self.row.options
        offered = f"  [{'|'.join(options)}]" if options else ""
        return f"{self.row.question_id}  (chunk {self.row.chunk_id}): {self.row.question}{offered}"


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

    origin: GardenProposalOrigin | Unrecognized
    routine_name: str | None
    created_by: str | None = None

    @property
    def rendered(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        tokens = [f"origin={self.origin}"]
        if self.routine_name is not None:
            tokens.append(f"routine={self.routine_name}")
        if self.origin == GardenProposalOrigin.OPERATOR and self.created_by is not None:
            tokens.append(f"created_by={self.created_by}")
        return "  ".join(tokens)
