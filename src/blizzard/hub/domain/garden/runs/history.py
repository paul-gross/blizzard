"""A garden routine's runs are readable — the run list and one run's own delta, over
`work_item_runs`, `finding_sets`, and the delivered artifact's own raw content (blizzard
gardening: runs are readable).

`work_item_runs` is the only table that makes an escalated or a still-running run
enumerable (`bzh:facts-not-status`) — a run's `outcome` is derived fresh from the
chunk's own facts every read, never stored, the way every other chunk status is. The
delta a delivered set actually published is read back from its own artifact, parsed as
`DeliveredDelta` — never reconstructed from `finding_facts` — and an add op is linked
to the finding id it minted positionally, by the order `GardenDelivery.deliver` wrote
both in; a set predating that linkage naturally yields no matched adds rather than a
fabricated one."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.garden.findings.model import Finding, IReadFindingRepository
from blizzard.hub.domain.garden.formats import (
    DeliveredDelta,
    FindingAddOp,
    FindingGoneOp,
    FindingObservedOp,
    IGardenFormats,
)
from blizzard.hub.domain.garden.runs.window import InvalidWindowError, require_until_after_since


@domain_model
@dataclass(frozen=True)
class DeliveredSet:
    """One `finding_sets` row a run delivered — the list read's own per-set shape
    (reported one entry per set, several sets from one run never merged into one).

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


@domain_model
@dataclass(frozen=True)
class RunEscalation:
    """The two things an escalated run carries, and nothing else (the hub records no
    escalation rationale): the escalating node — resolved to a human name by the
    caller, which alone holds the graph resolver — and the takeover command(s)."""

    graph_id: str
    node_id: str | None
    takeover_command: str
    wrapped_takeover_command: str


@domain_model
@dataclass(frozen=True)
class RunSummary:
    """One run in a time window — `list_runs`'s own row."""

    chunk_id: str
    routine_name: str
    scope_slug: str
    mode: str
    minted_at: datetime
    outcome: ChunkStatus
    escalation: RunEscalation | None
    delivered: list[DeliveredSet]


@domain_model
@dataclass(frozen=True)
class AddedFinding:
    """One `add` op a delivered set's artifact named — `finding_id` is the finding it
    minted, or `None` when the set predates the `finding_facts.finding_set_id` linkage
    and so cannot be matched back to one."""

    finding_id: str | None
    class_: str
    locus: str
    summary: str
    introduced: str | None


@domain_model
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


@domain_model
@dataclass(frozen=True)
class GoneFinding:
    """One `gone` op a delivered set's artifact named."""

    finding_id: str
    note: str


@domain_model
@dataclass(frozen=True)
class DeliveredSetDelta:
    """One delivered set's own published delta — added, observed, and gone kept as
    three distinct groups, never merged."""

    finding_set_id: str
    revisions: dict[str, str]
    measurement: str | None
    added: list[AddedFinding]
    observed: list[ObservedFinding]
    gone: list[GoneFinding]


@domain_model
@dataclass(frozen=True)
class RunDelta:
    """One run's full detail — `run_delta`'s own read: its identity, its derived
    outcome, and, per delivered set, the delta it actually published (several sets
    from one run stay separately grouped here too)."""

    chunk_id: str
    routine_name: str
    scope_slug: str
    mode: str
    outcome: ChunkStatus
    escalation: RunEscalation | None
    sets: list[DeliveredSetDelta]


@domain_model
@dataclass(frozen=True)
class RunIdentity:
    """One chunk's own run identity, joined through its `chunk_work_refs`/`work_items`
    pointer to its `work_item_runs` row — `run_delta`'s own identity read, unwindowed."""

    chunk_id: str
    routine_name: str
    scope_slug: str
    mode: str
    minted_at: datetime


@domain_model
@dataclass(frozen=True)
class RunDeliveries:
    """One run in a time window, plus every `finding_sets` row it delivered —
    `runs_in_window`'s own row, before outcome is derived from the chunk's own facts."""

    identity: RunIdentity
    delivered: list[DeliveredSet]


@domain_model
@dataclass(frozen=True)
class DeliveredSetRaw:
    """One delivered set's own artifact text, plus the finding ids its `add` facts
    minted in artifact order — `delivered_sets`'s own per-set read, the input
    `_set_delta` folds into a `DeliveredSetDelta`."""

    finding_set_id: str
    revisions: dict[str, str]
    measurement: str | None
    artifact_data: str
    add_finding_ids: list[str]


class IReadGardenRunRepository(Protocol):
    def runs_in_window(self, *, since: datetime, until: datetime) -> list[RunDeliveries]:
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


def _escalation(chunk_graph_id: str, facts: ChunkFacts) -> RunEscalation | None:
    """The open escalation a `NEEDS_HUMAN` chunk carries, or `None` on any other
    outcome — `ChunkFacts.status` returning `NEEDS_HUMAN` implies one exists."""
    escalation = facts.open_escalation()
    if escalation is None:
        return None
    # A migration recorded after the escalation opened can re-pin the chunk elsewhere while the escalation
    # stays open (a migration never supersedes it, `bzh:facts-not-status`), so `chunk.graph_id` alone
    # would read the new pin, not the one escalated from.
    movement = facts.latest_movement(as_of=escalation.recorded_at)
    if movement is None:
        graph_id, node_id = chunk_graph_id, None
    else:
        graph_id, node_id = movement.graph_id or chunk_graph_id, movement.node_id
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


def _observed_ids(delta: DeliveredDelta) -> list[str]:
    """Every finding id one parsed artifact's `observed` ops name, in artifact order."""
    return [op.id for op in delta.findings if isinstance(op, FindingObservedOp)]


def _set_delta(raw: DeliveredSetRaw, delta: DeliveredDelta, findings: Mapping[str, Finding]) -> DeliveredSetDelta:
    """Fold one delivered set's parsed artifact into its own added/observed/gone groups —
    an add op is zipped positionally against `raw.add_finding_ids`, never
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
        if isinstance(op, FindingAddOp):
            added.append(
                AddedFinding(
                    finding_id=next(add_ids, None),
                    class_=op.class_,
                    locus=op.locus,
                    summary=op.summary,
                    introduced=op.introduced,
                )
            )
        elif isinstance(op, FindingObservedOp):
            row = findings.get(op.id)
            observed.append(
                ObservedFinding(
                    finding_id=op.id,
                    class_=row.class_ if row is not None else None,
                    locus=row.locus if row is not None else None,
                    summary=row.summary if row is not None else None,
                )
            )
        elif isinstance(op, FindingGoneOp):
            gone.append(GoneFinding(finding_id=op.id, note=op.note))
    return DeliveredSetDelta(
        finding_set_id=raw.finding_set_id,
        revisions=raw.revisions,
        measurement=raw.measurement,
        added=added,
        observed=observed,
        gone=gone,
    )


@domain_model
@dataclass(frozen=True)
class RunWindow:
    """The ``[since, until)`` a run list reads — either edge optional, defaulting to
    the day ending now; inverted, empty, or wider than the span cap refuses."""

    since: datetime
    until: datetime

    #: The default span when ``since`` is not named.
    DEFAULT_SPAN = timedelta(hours=24)
    #: The span cap — a run list is bounded by window, not paged.
    MAX_SPAN_DAYS = 366

    @classmethod
    def of(cls, since: datetime | None, until: datetime | None, *, now: datetime) -> RunWindow:
        resolved_until = until if until is not None else now
        resolved_since = since if since is not None else resolved_until - cls.DEFAULT_SPAN
        require_until_after_since(resolved_since, resolved_until)
        if resolved_until - resolved_since > timedelta(days=cls.MAX_SPAN_DAYS):
            raise InvalidWindowError(f"since/until would span more than {cls.MAX_SPAN_DAYS} days")
        return cls(since=resolved_since, until=resolved_until)


def run_rows(
    records: Sequence[RunDeliveries],
    chunks_by_id: Mapping[str, Chunk],
    facts_by_id: Mapping[str, ChunkFacts],
) -> list[RunSummary]:
    """One row per run whose chunk still exists, its outcome derived from the chunk's
    facts. An ephemeral (grouped-away/deleted) chunk's run is absent from every read;
    a chunk with no facts yet reads as freshly minted."""
    rows: list[RunSummary] = []
    for record in records:
        chunk = chunks_by_id.get(record.identity.chunk_id)
        if chunk is None:
            continue
        facts = facts_by_id.get(record.identity.chunk_id) or ChunkFacts(minted=True)
        outcome, escalation = _outcome_and_escalation(chunk.graph_id, facts)
        rows.append(
            RunSummary(
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
        formats: IGardenFormats,
        clock: IClock,
    ) -> None:
        self._repo = repo
        self._chunk_records = chunk_records
        self._chunk_facts = chunk_facts
        self._findings = findings
        self._formats = formats
        self._clock = clock

    def list_runs(self, *, since: datetime | None = None, until: datetime | None = None) -> list[RunSummary]:
        """Every run in the window :meth:`RunWindow.of` settles against now. One bulk
        `get_many` and one bulk `load_facts_for` resolve every window run's chunk and
        chunk facts (`bzh:bulk-reconstitution`) — `records` is already the window's own
        bounded set (`runs_in_window`'s own SQL `WHERE`), so the batch cost tracks runs
        in the window."""
        window = RunWindow.of(since, until, now=self._clock.now())
        records = self._repo.runs_in_window(since=window.since, until=window.until)
        chunk_ids = [record.identity.chunk_id for record in records]
        return run_rows(records, self._chunk_records.get_many(chunk_ids), self._chunk_facts.load_facts_for(chunk_ids))

    def run_delta(self, chunk: Chunk) -> RunDelta | None:
        """`chunk` is already resolved (`bzh:domain-takes-objects`) — the caller 404s on
        an unknown chunk id before this is ever invoked; `None` here means only that
        `chunk` names no `work_item_runs`-backed run."""
        identity = self._repo.run_identity(chunk.chunk_id)
        if identity is None:
            return None
        facts = self._chunk_facts.load_facts(chunk.chunk_id) or ChunkFacts(minted=True)
        outcome, escalation = _outcome_and_escalation(chunk.graph_id, facts)
        parsed = [
            (raw, self._formats.finding_delta(raw.finding_set_id, raw.artifact_data))
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
