"""A garden routine's runs are readable — the run list and one run's own delta, over
`work_item_runs`, `finding_sets`, and the delivered artifact's own raw content (blizzard
gardening: runs are readable, Phase 2).

`work_item_runs` is the only table that makes an escalated or a still-running run
enumerable (`bzh:facts-not-status`) — a run's `outcome` is derived fresh from the
chunk's own facts every read, never stored, the way every other chunk status is. The
delta a delivered set actually published is read back from its own artifact, parsed as
`FindingDelta` (D2) — never reconstructed from `finding_facts` — and an add op is linked
to the finding id it minted positionally, by the order `GardenDelivery.deliver` wrote
both in; a set predating that linkage naturally yields no matched adds rather than a
fabricated one."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunks.record import IReadChunkRecordRepository
from blizzard.hub.domain.findings import Finding, IReadFindingRepository
from blizzard.hub.domain.garden_delivery import parse_delta
from blizzard.hub.domain.work import Chunk, ChunkFacts
from blizzard.wire.finding import AddFindingOp, FindingDelta, GoneFindingOp, ObservedFindingOp


@dataclass(frozen=True)
class DeliveredSet:
    """One `finding_sets` row a run delivered — the list read's own per-set shape
    (D4: reported one entry per set, several sets from one run never merged into one).

    `added_count`/`observed_count`/`gone_count` are how many `add`/`observed`/`gone`
    facts *this delivery* recorded on `finding_facts.finding_set_id` — this delivery's
    own act, never a finding's whole life history, and never merged across sets. Named
    with the `_count` suffix so a reader cannot mistake them for `DeliveredSetDelta`'s
    own full `added`/`observed`/`gone` lists; `0`, never `None`, when a kind is absent."""

    finding_set_id: str
    revisions: dict[str, str]
    measurement: str | None
    added_count: int
    observed_count: int
    gone_count: int


@dataclass(frozen=True)
class RunEscalation:
    """The two things an escalated run carries, and nothing else (the hub records no
    escalation rationale): the escalating node — resolved to a human name by the
    caller, which alone holds the graph resolver — and the takeover command(s)."""

    graph_id: str
    node_id: str | None
    takeover_command: str
    wrapped_takeover_command: str


@dataclass(frozen=True)
class RunRow:
    """One run in a time window — `list_runs`'s own row."""

    chunk_id: str
    routine_name: str
    scope_slug: str
    mode: str
    minted_at: datetime
    outcome: ChunkStatus
    escalation: RunEscalation | None
    delivered: list[DeliveredSet]


@dataclass(frozen=True)
class AddedFinding:
    """One `add` op a delivered set's artifact named — `finding_id` is the finding it
    minted, or `None` when the set predates the `finding_facts.finding_set_id` linkage
    (Phase 1) and so cannot be matched back to one."""

    finding_id: str | None
    class_: str
    locus: str
    summary: str
    introduced: str | None


@dataclass(frozen=True)
class ObservedFinding:
    """One `observed` op a delivered set's artifact named. The artifact repeats no
    descriptive field for a finding it is merely re-observing, so `class_`/`locus`/
    `summary` are read back from the finding row the id names — each `None` when the id
    names no row, so an observed entry still renders by id rather than being dropped."""

    finding_id: str
    class_: str | None
    locus: str | None
    summary: str | None


@dataclass(frozen=True)
class GoneFinding:
    """One `gone` op a delivered set's artifact named."""

    finding_id: str
    note: str


@dataclass(frozen=True)
class DeliveredSetDelta:
    """One delivered set's own published delta — added, observed, and gone kept as
    three distinct groups (D4), never merged."""

    finding_set_id: str
    revisions: dict[str, str]
    measurement: str | None
    added: list[AddedFinding]
    observed: list[ObservedFinding]
    gone: list[GoneFinding]


@dataclass(frozen=True)
class RunDelta:
    """One run's full detail — `run_delta`'s own read: its identity, its derived
    outcome, and, per delivered set, the delta it actually published (D4: several sets
    from one run stay separately grouped here too)."""

    chunk_id: str
    routine_name: str
    scope_slug: str
    mode: str
    outcome: ChunkStatus
    escalation: RunEscalation | None
    sets: list[DeliveredSetDelta]


@dataclass(frozen=True)
class RunIdentity:
    """One chunk's own run identity, joined through its `chunk_work_refs`/`work_items`
    pointer to its `work_item_runs` row — `run_delta`'s own identity read, unwindowed."""

    chunk_id: str
    routine_name: str
    scope_slug: str
    mode: str
    minted_at: datetime


@dataclass(frozen=True)
class RunRecord:
    """One run in a time window, plus every `finding_sets` row it delivered —
    `runs_in_window`'s own row, before outcome is derived from the chunk's own facts."""

    identity: RunIdentity
    delivered: list[DeliveredSet]


@dataclass(frozen=True)
class DeliveredSetRaw:
    """One delivered set's own artifact text, plus the finding ids its `add` facts
    minted in artifact order (D1) — `delivered_sets`'s own per-set read, the input
    `_set_delta` folds into a `DeliveredSetDelta`."""

    finding_set_id: str
    revisions: dict[str, str]
    measurement: str | None
    artifact_data: str
    add_finding_ids: list[str]


class IReadGardenRunRepository(Protocol):
    def runs_in_window(self, *, since: datetime, until: datetime) -> list[RunRecord]:
        """Every `work_item_runs`-backed chunk minted in `[since, until)`, newest first
        (an explicit SQL `order_by`, never incidental row order) — each with the
        `finding_sets` rows it delivered, if any."""
        ...

    def run_identity(self, chunk_id: str) -> RunIdentity | None:
        """`chunk_id`'s own run identity, or `None` when it names no
        `work_item_runs`-backed chunk."""
        ...

    def delivered_sets(self, chunk_id: str) -> list[DeliveredSetRaw]:
        """Every `finding_sets` row `chunk_id` delivered, each with its own artifact's
        raw text and the finding ids its `add` facts minted, in artifact order."""
        ...


def _movement_as_of(facts: ChunkFacts, at: datetime, *, default_graph_id: str) -> tuple[str, str | None] | None:
    """The `(graph_id, node_id)` the chunk stood on at `at` — `ChunkFacts.latest_movement`'s
    own family ranking, restricted to movements no later than `at`. Needed because a
    migration recorded after an escalation opened can re-pin the chunk elsewhere while
    the escalation stays open (a migration never supersedes it, `bzh:facts-not-status`),
    so `chunk.graph_id` alone would read the new pin, not the one escalated from."""
    ranked: list[tuple[datetime, int, int, str, str | None]] = []
    transitions = [t for t in facts.transitions if t.recorded_at <= at]
    if transitions:
        t = max(transitions, key=lambda t: (t.recorded_at, t.epoch))
        ranked.append((t.recorded_at, t.epoch, 0, t.graph_id or default_graph_id, t.to_node_id))
    migrations = [m for m in facts.migrations if m.recorded_at <= at]
    if migrations:
        m = max(migrations, key=lambda m: (m.recorded_at, m.epoch))
        ranked.append((m.recorded_at, m.epoch, 1, m.to_graph_id, m.landed_node_id))
    restarts = [r for r in facts.restarts if r.recorded_at <= at]
    if restarts:
        r = max(restarts, key=lambda r: (r.recorded_at, r.epoch))
        ranked.append((r.recorded_at, r.epoch, 2, r.graph_id, r.to_node_id))
    if not ranked:
        return None
    _, _, _, graph_id, node_id = max(ranked, key=lambda entry: entry[:3])
    return graph_id, node_id


def _escalation(chunk_graph_id: str, facts: ChunkFacts) -> RunEscalation | None:
    """The open escalation a `NEEDS_HUMAN` chunk carries, or `None` on any other
    outcome — `ChunkFacts.status` returning `NEEDS_HUMAN` implies one exists."""
    escalation = facts.open_escalation()
    if escalation is None:
        return None
    movement = _movement_as_of(facts, escalation.recorded_at, default_graph_id=chunk_graph_id)
    graph_id, node_id = movement if movement is not None else (chunk_graph_id, None)
    return RunEscalation(
        graph_id=graph_id,
        node_id=node_id,
        takeover_command=escalation.takeover_command,
        wrapped_takeover_command=escalation.wrapped_takeover_command,
    )


def _outcome_and_escalation(chunk_graph_id: str, facts: ChunkFacts) -> tuple[ChunkStatus, RunEscalation | None]:
    outcome = facts.status()
    if outcome is not ChunkStatus.NEEDS_HUMAN:
        return outcome, None
    return outcome, _escalation(chunk_graph_id, facts)


def _observed_ids(delta: FindingDelta) -> list[str]:
    """Every finding id one parsed artifact's `observed` ops name, in artifact order."""
    return [op.id for op in delta.findings if isinstance(op, ObservedFindingOp)]


def _set_delta(raw: DeliveredSetRaw, delta: FindingDelta, findings: Mapping[str, Finding]) -> DeliveredSetDelta:
    """Fold one delivered set's parsed artifact into its own added/observed/gone groups —
    an add op is zipped positionally against `raw.add_finding_ids` (D1), never
    `strict`: a set predating the `finding_facts.finding_set_id` linkage carries no add
    ids at all, and every add on it degrades to an unmatched `finding_id=None` rather
    than raising or fabricating one. An observed op degrades the same way in the other
    direction: an id `findings` holds no row for keeps its descriptive fields `None`,
    rather than dropping the entry or inventing text for it."""
    add_ids = iter(raw.add_finding_ids)
    added: list[AddedFinding] = []
    observed: list[ObservedFinding] = []
    gone: list[GoneFinding] = []
    for op in delta.findings:
        if isinstance(op, AddFindingOp):
            added.append(
                AddedFinding(
                    finding_id=next(add_ids, None),
                    class_=op.class_,
                    locus=op.locus,
                    summary=op.summary,
                    introduced=op.introduced,
                )
            )
        elif isinstance(op, ObservedFindingOp):
            row = findings.get(op.id)
            observed.append(
                ObservedFinding(
                    finding_id=op.id,
                    class_=row.class_ if row is not None else None,
                    locus=row.locus if row is not None else None,
                    summary=row.summary if row is not None else None,
                )
            )
        elif isinstance(op, GoneFindingOp):
            gone.append(GoneFinding(finding_id=op.id, note=op.note))
    return DeliveredSetDelta(
        finding_set_id=raw.finding_set_id,
        revisions=raw.revisions,
        measurement=raw.measurement,
        added=added,
        observed=observed,
        gone=gone,
    )


class GardenRunService:
    """Reads a routine run's list and one run's own delta, deriving `outcome` from the
    chunk's own facts (`bzh:facts-not-status`) rather than any stored column."""

    def __init__(
        self,
        *,
        repo: IReadGardenRunRepository,
        chunk_records: IReadChunkRecordRepository,
        chunk_facts: IReadChunkFactsRepository,
        findings: IReadFindingRepository,
    ) -> None:
        self._repo = repo
        self._chunk_records = chunk_records
        self._chunk_facts = chunk_facts
        self._findings = findings

    def list_runs(self, *, since: datetime, until: datetime) -> list[RunRow]:
        """`chunk_records`/`chunk_facts` are read one chunk at a time — `records` is
        already the window's own bounded set (`runs_in_window`'s own SQL `WHERE`), so
        the per-row cost tracks runs in the window, not `list_all`/`load_all_facts`'s
        whole-store cost, which would grow with every chunk the fleet has ever minted
        regardless of how few runs the window actually names."""
        records = self._repo.runs_in_window(since=since, until=until)
        rows: list[RunRow] = []
        for record in records:
            chunk = self._chunk_records.get(record.identity.chunk_id)
            if chunk is None:
                continue  # an ephemeral (grouped-away/deleted) chunk's run is absent from every read
            facts = self._chunk_facts.load_facts(record.identity.chunk_id) or ChunkFacts(minted=True)
            outcome, escalation = _outcome_and_escalation(chunk.graph_id, facts)
            rows.append(
                RunRow(
                    chunk_id=record.identity.chunk_id,
                    routine_name=record.identity.routine_name,
                    scope_slug=record.identity.scope_slug,
                    mode=record.identity.mode,
                    minted_at=record.identity.minted_at,
                    outcome=outcome,
                    escalation=escalation,
                    delivered=record.delivered,
                )
            )
        return rows

    def run_delta(self, chunk: Chunk) -> RunDelta | None:
        """`chunk` is already resolved (`bzz:domain-takes-objects`) — the caller 404s on
        an unknown chunk id before this is ever invoked; `None` here means only that
        `chunk` names no `work_item_runs`-backed run."""
        identity = self._repo.run_identity(chunk.chunk_id)
        if identity is None:
            return None
        facts = self._chunk_facts.load_facts(chunk.chunk_id) or ChunkFacts(minted=True)
        outcome, escalation = _outcome_and_escalation(chunk.graph_id, facts)
        parsed = [
            (raw, parse_delta(raw.finding_set_id, raw.artifact_data))
            for raw in self._repo.delivered_sets(chunk.chunk_id)
        ]
        # Every observed id the run named, across all its sets, is read in one batched
        # lookup: the descriptive fields an observed op omits live on the finding row,
        # and a per-id read would cost one query pair per re-observed finding.
        rows = self._findings.get_many([fid for _, delta in parsed for fid in _observed_ids(delta)])
        sets = [_set_delta(raw, delta, rows) for raw, delta in parsed]
        return RunDelta(
            chunk_id=chunk.chunk_id,
            routine_name=identity.routine_name,
            scope_slug=identity.scope_slug,
            mode=identity.mode,
            outcome=outcome,
            escalation=escalation,
            sets=sets,
        )
