"""What an operator verb prints in text mode — one view per rendered payload, each
holding its payload verbatim, so ``status`` and the list verbs render a row alike."""

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
        return f"{self.row['chunk_id']}  {self.row['status']:<16} @ {self.node}  {rendered:>10}"


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


class ChunkListing(Listing):
    empty = "no chunks"

    def line(self, row: Any) -> str:
        return ChunkRow(row).line()


class RunnerListing(Listing):
    empty = "no runners registered"

    def line(self, row: Any) -> str:
        return RunnerRow(row).line()


class QuestionListing(Listing):
    empty = "no open questions"

    def line(self, row: Any) -> str:
        return QuestionRow(row).line()


class GraphListing(Listing):
    empty = "no graphs minted yet"

    def line(self, row: Any) -> str:
        marker = "effective" if row["effective"] else ("retired" if row["retired"] else "superseded")
        return f"{row['graph_id']}  name={row['name']}  {marker}  created_at={row['created_at']}"


class GraphSyncListing(Listing):
    empty = "no packaged graphs to reconcile"

    def line(self, row: Any) -> str:
        detail = f" — {row['detail']}" if row.get("detail") else ""
        graph_id = f" {row['graph_id']}" if row.get("graph_id") else ""
        return f"{row['name']}: {row['status']}{graph_id}{detail}"


class QueueListing(Listing):
    empty = "queue is empty"

    def line(self, row: Any) -> str:
        return f"{row['position']}  {row['chunk_id']}  graph={row.get('graph_id')}"


class DecisionListing(Listing):
    empty = "no open decisions"

    def line(self, row: Any) -> str:
        choices = ", ".join(c["name"] for c in row.get("choices", []))
        return f"{row['decision_id']}  chunk={row['chunk_id']}  node={row['node_name']}  choices=[{choices}]"


class EventListing(Listing):
    empty = "no events"

    def line(self, row: Any) -> str:
        occurred = row.get("occurred_at") or "-"
        tool = f"  tool={row['tool']}" if row.get("tool") else ""
        subject = f"  {row['subject']}" if row.get("subject") else ""
        return f"{occurred}  {row['kind']:<16} chunk={row['chunk_id']} node={row['node_id']}{tool}{subject}"


class CountsListing(Listing):
    empty = "no counts"

    def line(self, row: Any) -> str:
        return f"{row['key']}: {row['count']}"


class DurationsListing(Listing):
    empty = "no durations"

    def line(self, row: Any) -> str:
        return (
            f"{row['key']}  steps={row['completed_steps']}  "
            f"total={row['total_seconds']:.1f}s  avg={row['avg_seconds']:.1f}s"
        )


@dataclass(frozen=True)
class SpendRow:
    """One grouping key's spend rollup — the key column varies (``key`` for
    node/graph, ``chunk_id`` for the per-chunk dataset), so the caller names it."""

    row: dict[str, Any]
    key_field: str = "key"

    def line(self) -> str:
        row = self.row
        cost = Cost(row["cost_usd"], row["cost_partial"]).rendered
        return (
            f"{row[self.key_field]}  {cost}  in={row['input_tokens']} out={row['output_tokens']} "
            f"cache_read={row['cache_read_tokens']} cache_create={row['cache_create_tokens']}"
        )


class SpendListing(Listing):
    empty = "no spend rollups"

    def line(self, row: Any) -> str:
        return SpendRow(row).line()


class ChunkSpendListing(Listing):
    empty = "no chunk spend rollups"

    def line(self, row: Any) -> str:
        return SpendRow(row, key_field="chunk_id").line()


class OutcomesListing(Listing):
    empty = "no outcomes"

    def line(self, row: Any) -> str:
        choices = ", ".join(f"{name}={count}" for name, count in row["choice_counts"].items())
        return f"{row['node_id']}  choices=[{choices}]  attempt_failures={row['attempt_failures']}"


class WorkItemListing(Listing):
    empty = "no work items"

    def line(self, row: Any) -> str:
        label = row.get("label") or f"{row['source']}#{row['ref']}"
        if row.get("error"):
            return f"{label}: error — {row['error']}"
        return f"{label}: {row.get('title') or '(no title)'}"


@dataclass(frozen=True)
class ChunkDetail:
    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        body = self.body
        yield f"{body['chunk_id']}  status={body['status']}  graph={body.get('graph_name') or body['graph_id']}"
        yield f"  node: {ChunkRow(body).node}"
        # Both defaults on their own line: `chunk set` can write either, so a text-mode
        # read-back exists for both. `-` is "express no preference", not unknown.
        models = ", ".join(body.get("default_model") or []) or "-"
        yield f"  default model: {models}   default effort: {body.get('default_effort') or '-'}"
        pointers = body.get("work_refs") or []
        if pointers:
            labels = ", ".join(p.get("label") or f"{p['source']}#{p['ref']}" for p in pointers)
            yield f"  pointers: {labels}"
        route = body.get("route")
        if route:
            yield f"  runner: {route['runner_id']}  environments: {len(route.get('environment_ids', []))}"
        yield f"  cost: {Cost.of(body.get('cost')).rendered}"


@dataclass(frozen=True)
class MigrationIntent:
    chunk_id: str
    body: dict[str, Any]
    cancelled: bool

    def lines(self) -> Iterator[str]:
        if self.cancelled:
            yield f"cleared {self.chunk_id}'s standing migration intent"
            return
        intent = self.body.get("intended_migration")
        if intent is None:
            # Shouldn't happen for a successful set, but degrade legibly rather than raise.
            yield f"{self.chunk_id}: migration intent not set"
            return
        target = intent.get("graph_name") or intent.get("graph_id")
        if intent.get("mode") == "forced":
            yield f"{self.chunk_id} will migrate to {target} node {intent.get('node_name')} at its next transition"
        else:
            yield f"{self.chunk_id} will auto-migrate to {target} at its next transition (name-matched node)"


@dataclass(frozen=True)
class RunnerDetail:
    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        yield f"{self.body['runner_id']}  {RunnerRow(self.body).liveness}  ws={self.body.get('workspace_id', '-')}"
        yield f"  hub_paused={self.body.get('hub_paused')}  locally_paused={self.body.get('locally_paused')}"


@dataclass(frozen=True)
class GraphDetail:
    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        body = self.body
        marker = "retired" if body.get("retired") else "enabled"
        yield f"{body['graph_id']}  name={body['name']}  {marker}  entry={body.get('entry_node_id')}"
        for session in body.get("sessions", []):
            yield f"  session {session['name']}  {self._session_summary(session)}"
        for node in body.get("nodes", []):
            yield f"  node {node['node_id']}  name={node['name']}  executor={node.get('executor')}"
        for edge in body.get("edges", []):
            yield f"  edge {edge['from_node_id']} --[{edge.get('choice_id')}]--> {edge.get('to_node_name')}"

    @staticmethod
    def _session_summary(session: dict[str, Any]) -> str:
        parts = []
        if session.get("model"):
            parts.append(f"model={','.join(session['model'])}")
        if session.get("effort"):
            parts.append(f"effort={session['effort']}")
        if session.get("compaction_window"):
            parts.append(f"compaction_window={session['compaction_window']}")
        rotate = session.get("rotate") or {}
        bounds = ", ".join(f"{k}={v}" for k, v in rotate.items() if v is not None)
        if bounds:
            parts.append(f"rotate=({bounds})")
        return "  ".join(parts) if parts else "(no pinning)"


@dataclass(frozen=True)
class FleetStatus:
    chunks: list[dict[str, Any]]
    runners: list[dict[str, Any]]
    questions: list[dict[str, Any]]
    spend: dict[str, Any]

    def lines(self) -> Iterator[str]:
        yield f"chunks ({len(self.chunks)}):"
        for chunk in self.chunks:
            yield f"  {ChunkRow(chunk, prefer_node_name=False).line()}"
        yield f"\nrunners ({len(self.runners)}):"
        for runner in self.runners:
            yield f"  {RunnerRow(runner).line()}"
        yield f"\nopen questions ({len(self.questions)}):"
        for question in self.questions:
            yield f"  {QuestionRow(question).line()}"
        yield f"\nfleet spend (all time): {Cost(self.spend['cost_usd'], self.spend['cost_partial']).rendered}"
