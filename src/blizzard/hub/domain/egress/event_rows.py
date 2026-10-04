"""The ``events`` egress rows, as typed records: a derivation, its events, and a segment's drop.

Contract: ``blizzard-product:/plans/fact-egress/events/spec/rows.md``. Pure: a derivation (or a drop fact) and its
chunk's :class:`StepFacts` in, rows out. Step columns come from the chunk's runner step at the segment's epoch, the
same position :func:`~blizzard.hub.domain.egress.rows.step_row` reports, never from an event's stored graph stamp.
Values are typed, not formatted: turning times and bools into a file format is the writer's job."""

from __future__ import annotations

import hashlib
import hmac
import posixpath
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from blizzard.foundation.roles import domain_model, dto
from blizzard.hub.domain.analytics.events import DerivationMarker, DropFact, SegmentProvenance, TranscriptEvent
from blizzard.hub.domain.egress.assembly import runner_step
from blizzard.hub.domain.egress.rows import trace_id_text
from blizzard.hub.domain.tracing.facts import StepFacts
from blizzard.hub.domain.tracing.steps import identify_steps
from blizzard.hub.egress.writer import rfc3339_utc

__all__ = [
    "RECORD_DERIVATION",
    "RECORD_DROPPED",
    "RECORD_EVENT",
    "EventDerivation",
    "ExportedEventsEntry",
    "FilePathMode",
    "FilePathPolicy",
    "derivation_id",
    "derivation_rows",
    "dropped_row",
    "missing_key_reason",
]

RECORD_DERIVATION = "derivation"
RECORD_EVENT = "event"
RECORD_DROPPED = "dropped"

_DERIVATION_ID_PREFIX = "blizzard-derivation/v1/"
_FILE_READ = "file_read"

type FilePathMode = Literal["relative", "hashed", "absolute", "omit"]


@domain_model
@dataclass(frozen=True)
class FilePathPolicy:
    """What leaves as a ``file_read`` event's subject. ``relative`` and ``hashed`` hash some or all paths, so they
    require ``key``; a policy without one cannot be built."""

    mode: FilePathMode
    key: bytes | None = None

    def __post_init__(self) -> None:
        if self.mode in ("relative", "hashed") and not self.key:
            raise ValueError(f"file_paths mode {self.mode!r} requires a non-empty hash key")

    def subject(self, path: str, working_directory: str | None) -> str | None:
        """``path`` as it leaves under this policy; ``working_directory`` is the segment's, never itself exported."""
        if self.mode == "omit":
            return None
        if self.mode == "absolute":
            return path
        if self.mode == "relative" and working_directory:
            inside = _relative_to(path, working_directory)
            if inside is not None:
                return inside
        return self._hash(path)

    def _hash(self, path: str) -> str:
        assert self.key is not None
        return hmac.new(self.key, path.encode(), hashlib.sha256).hexdigest()


def missing_key_reason(variable: str) -> str:
    """Why the ``events`` dataset is off: its file path policy hashes paths and ``variable`` holds no key."""
    return f"the events dataset is off: egress.path_key_env names {variable}, which is unset or empty"


def _relative_to(path: str, working_directory: str) -> str | None:
    """``path`` below ``working_directory`` on a separator boundary, or ``None`` when it is not a file inside it."""
    root = posixpath.normpath(working_directory)
    resolved = posixpath.normpath(path if posixpath.isabs(path) else posixpath.join(root, path))
    prefix = root if root.endswith("/") else root + "/"
    if resolved == root or not resolved.startswith(prefix):
        return None
    return resolved[len(prefix) :]


@dto
@dataclass(frozen=True)
class EventDerivation:
    """One derivation as the store holds it: the marker, the segment's frozen identity, and its events.

    ``chunk_id`` and ``epoch`` ride on the derivation because an empty one has no event to take them from."""

    marker: DerivationMarker
    provenance: SegmentProvenance
    events: tuple[TranscriptEvent, ...]
    chunk_id: str
    epoch: int
    spawn_generation: int
    spawn_cwd: str | None


@dto
@dataclass(frozen=True)
class ExportedEventsEntry:
    """One ``events`` row; field order is the column order. A column that does not apply to the record type is
    ``None``."""

    record_type: str
    segment_id: str
    extractor_version: str | None
    derivation_id: str | None
    derived_at: datetime | None
    complete: bool | None
    event_count: int | None
    dropped_at: datetime | None
    kind: str | None
    subject: str | None
    tool: str | None
    turn_path: str | None
    occurrence: int | None
    occurred_at: datetime | None
    depth: int | None
    agent_type: str | None
    step_key: str
    trace_id: str
    step_started_at: datetime
    chunk_id: str
    epoch: int
    spawn_generation: int
    graph_id: str
    graph_name: str
    node_id: str
    node_name: str
    harness_id: str | None
    harness_version: str | None
    model: str | None
    effort: str | None
    exported_at: datetime


def derivation_id(segment_id: str, extractor_version: str, derived_at: datetime) -> str:
    """The first 16 bytes, as hex, of SHA-256 over ``blizzard-derivation/v1/<segment>/<version>/<derived_at>``.

    ``derived_at`` is ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` in UTC, the text the NDJSON ``derived_at`` column carries, so a
    reader can recompute the id from a row."""
    text = f"{_DERIVATION_ID_PREFIX}{segment_id}/{extractor_version}/{rfc3339_utc(derived_at)}"
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def _anchored(
    record_type: str, segment_id: str, facts: StepFacts, chunk_id: str, epoch: int, spawn_generation: int, at: datetime
) -> ExportedEventsEntry:
    """A row of ``record_type`` carrying only the columns every record has: the segment's step, position and
    ``exported_at``."""
    if chunk_id != facts.chunk_id:
        raise ValueError(f"segment {segment_id} belongs to {chunk_id}, not {facts.chunk_id}")
    step = runner_step(identify_steps(facts), epoch)
    if step is None:
        raise LookupError(f"segment {segment_id} has no runner step at epoch {epoch} of {facts.chunk_id}")
    return ExportedEventsEntry(
        record_type=record_type,
        segment_id=segment_id,
        extractor_version=None,
        derivation_id=None,
        derived_at=None,
        complete=None,
        event_count=None,
        dropped_at=None,
        kind=None,
        subject=None,
        tool=None,
        turn_path=None,
        occurrence=None,
        occurred_at=None,
        depth=None,
        agent_type=None,
        step_key=step.key.text(),
        trace_id=trace_id_text(step.key),
        step_started_at=step.start,
        chunk_id=chunk_id,
        epoch=epoch,
        spawn_generation=spawn_generation,
        graph_id=step.position.graph_id,
        graph_name=facts.graphs[step.position.graph_id].name,
        node_id=step.position.node_id,
        node_name=step.position.node_name,
        harness_id=None,
        harness_version=None,
        model=None,
        effort=None,
        exported_at=at,
    )


def derivation_rows(
    facts: StepFacts, derivation: EventDerivation, paths: FilePathPolicy, exported_at: datetime
) -> tuple[ExportedEventsEntry, ...]:
    """The ``derivation`` row, then one ``event`` row per event, positioned by the runner step at the segment's epoch.

    Raises :class:`ValueError` when the derivation belongs to another chunk and :class:`LookupError` when the facts
    hold no runner step at its epoch."""
    marker = derivation.marker
    provenance = derivation.provenance
    base = _anchored(
        RECORD_DERIVATION,
        marker.segment_id,
        facts,
        derivation.chunk_id,
        derivation.epoch,
        derivation.spawn_generation,
        exported_at,
    )
    keyed = replace(
        base,
        extractor_version=marker.extractor_version,
        derivation_id=derivation_id(marker.segment_id, marker.extractor_version, marker.derived_at),
        derived_at=marker.derived_at,
    )
    rows = [replace(keyed, complete=marker.complete, event_count=len(derivation.events))]
    for event in derivation.events:
        subject = event.subject
        if event.kind == _FILE_READ and subject is not None:
            subject = paths.subject(subject, derivation.spawn_cwd)
        rows.append(
            replace(
                keyed,
                record_type=RECORD_EVENT,
                kind=event.kind,
                subject=subject,
                tool=event.tool,
                turn_path=event.turn_path,
                occurrence=event.occurrence,
                occurred_at=event.occurred_at,
                depth=event.depth,
                agent_type=event.agent_type,
                harness_id=provenance.harness_id,
                harness_version=provenance.harness_version,
                model=provenance.model,
                effort=provenance.effort,
            )
        )
    return tuple(rows)


def dropped_row(facts: StepFacts, drop: DropFact, exported_at: datetime) -> ExportedEventsEntry:
    """The ``dropped`` row of one drop fact, positioned by the runner step at the segment's epoch.

    Raises :class:`ValueError` and :class:`LookupError` as :func:`derivation_rows` does."""
    base = _anchored(
        RECORD_DROPPED, drop.segment_id, facts, drop.chunk_id, drop.epoch, drop.spawn_generation, exported_at
    )
    return replace(base, dropped_at=drop.dropped_at)
