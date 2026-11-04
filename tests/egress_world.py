"""Helpers for the egress sweep tests, shared with other test modules."""

from __future__ import annotations

import gzip
import itertools
import json
import threading
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal, TypedDict, Unpack

import duckdb
import pytest
import sqlalchemy as sa

from blizzard.hub.domain.graph.model import Graph
from blizzard.hub.domain.observability.analytics.events import SegmentProvenance, TranscriptEvent
from blizzard.hub.domain.observability.analytics.extraction import EXTRACTOR_VERSION
from blizzard.hub.domain.observability.egress import sweep as sweep_module
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.hub.domain.observability.egress.event_rows import FilePathPolicy
from blizzard.hub.domain.observability.egress.sweep import EgressSweep
from blizzard.hub.domain.observability.tracing.repository import IReadTraceSteps
from blizzard.hub.domain.observability.transcripts import TranscriptSlice
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.egress.writer import (
    ColumnSpec,
    ColumnType,
    DatasetSchema,
    EgressBatch,
    EgressPass,
    EgressValues,
    IEgressWriter,
)
from blizzard.hub.store import schema
from blizzard.hub.store.internal.egress_store import EgressStore
from blizzard.hub.store.internal.hub_scoped_reads import build_hub_scoped_reads
from blizzard.hub.store.internal.transcript_event_store import TranscriptEventStore
from tests.hub_event_rows import hub_event_kinds, hub_event_log_of, hub_events_of
from tests.repo_files import repo_root
from tests.support import (
    HubHarness,
    InMemoryEgressWriter,
    SeedingTranscriptSegmentStore,
    default_project,
    hub_level_connections,
    hub_scoped,
    hub_store_connections,
    ingest,
)
from tests.trace_hub import claim, pass_build, trace_hub, trace_store

SETTLED = EgressConfig(directory=Path("unused"), settle_seconds=0, sweep_seconds=60)


_SEQ = itertools.count(1000)


_PATHS = FilePathPolicy("absolute")


def egress_hub(tmp_path: Path, config: EgressConfig | None = None) -> tuple[HubHarness, Graph]:
    return trace_hub(tmp_path, egress=config) if config is not None else trace_hub(tmp_path)


def egress_store(hub: HubHarness) -> EgressStore:
    return EgressStore(
        hub_store_connections(hub.engine),
        hub_events=hub_events_of(hub.engine),
        usage=build_hub_scoped_reads(hub_level_connections(hub.engine)).corpus,
    )


def egress_sweep(
    hub: HubHarness,
    writer: IEgressWriter,
    config: EgressConfig = SETTLED,
    *,
    store: EgressStore | None = None,
    pass_lock: threading.Lock | None = None,
    steps: IReadTraceSteps | None = None,
) -> EgressSweep:
    connections = hub_store_connections(hub.engine)
    corpus = build_hub_scoped_reads(hub_level_connections(hub.engine)).corpus
    return EgressSweep(
        steps=steps or trace_store(hub),
        egress=store or EgressStore(connections, hub_events=hub_events_of(hub.engine), usage=corpus),
        event_reads=corpus,
        paths=_PATHS,
        writer=writer,
        events=hub_event_log_of(hub.engine),
        clock=hub.clock,
        config=config,
        pass_lock=pass_lock,
    )


def _push_usage(hub: HubHarness, chunk_id: str, node_id: str, *, cost: float | None = 0.5, late: bool = False) -> None:
    payload = {
        "chunk_id": chunk_id,
        "node_id": node_id,
        "epoch": 1,
        "kind": "spawn",
        "model": "claude-opus-4-8",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 10,
        "cache_create_tokens": 5,
        "cost_usd": cost,
    }
    if late:
        payload.update(
            kind="resume",
            input_tokens=11,
            output_tokens=7,
            cache_read_tokens=5,
            cache_create_tokens=3,
            estimated_cost_usd=0.125,
        )
    resp = hub.client.post(
        "/api/fleet/events",
        json={"runner_id": "r1", "facts": [{"seq": next(_SEQ), "kind": "usage.recorded", "payload": payload}]},
    )
    assert resp.status_code == 200, resp.text


def graph_node_id(graph: Graph, name: str = "build") -> str:
    return next(n.node_id for n in graph.nodes if n.name == name)


def closed_step(hub: HubHarness, graph: Graph, ref: int, *, usage: int = 1) -> str:
    """A chunk claimed, billed ``usage`` times, and moved out of ``build`` — one closed runner step."""
    chunk_id = ingest(hub, [{"source": "default", "ref": str(ref)}])
    claim(hub, chunk_id, seq=next(_SEQ))
    for _ in range(usage):
        _push_usage(hub, chunk_id, graph_node_id(graph))
    hub.clock.advance(timedelta(seconds=5))
    pass_build(hub, chunk_id, graph)
    return chunk_id


def kinds(hub: HubHarness) -> list[str]:
    return hub_event_kinds(hub.engine, "egress-")


def egress_cursor_rows(hub: HubHarness) -> int:
    with hub.engine.connect() as conn:
        return conn.execute(hub_scoped(sa.select(sa.func.count()).select_from(schema.egress_cursor))).scalar_one()


def egress_rows(writer: InMemoryEgressWriter, dataset: str) -> list[dict[str, object]]:
    return [dict(row.values) for b in writer.batches if b.schema.name == dataset for row in b.rows]


def project_of(row: dict[str, object]) -> tuple[object, object, object]:
    return (row["project_id"], row["project_slug"], row["project_name"])


def home_project(hub: HubHarness) -> tuple[str, str, str]:
    """The default tenant's only project as ``(project_id, slug, name)``."""
    project = hub.services.projects.get(ProjectId(default_project(hub.engine)))
    assert project is not None
    return (project.project_id, project.slug, project.name)


OLD_VERSION = "blizzard-analytics/0"


_PLANTED = "PLANTED-PROMPT-TEXT"


_PROVENANCE = SegmentProvenance("claude_code", "1.0", "claude-sonnet-5", "high")


class _EgressFields(TypedDict, total=False):
    batch_limit: int
    extractor_versions: Literal["current", "all"]
    file_paths: Literal["relative", "hashed", "absolute", "omit"]


def _config(tmp_path: Path, **extra: Unpack[_EgressFields]) -> EgressConfig:
    fields: _EgressFields = {"file_paths": "absolute"}
    fields.update(extra)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    return EgressConfig(
        directory=tmp_path / "out",
        settle_seconds=0,
        min_free_bytes=0,
        **fields,
    )


class Events:
    """A hub with the export on, a closed runner step, and that step's transcript segments, derived by hand."""

    def __init__(self, tmp_path: Path, *, path_key: bytes | None = None, **extra: Unpack[_EgressFields]) -> None:
        self.directory = tmp_path / "out"
        self.hub, self.graph = trace_hub(tmp_path, egress=_config(tmp_path, **extra), egress_path_key=path_key)
        self.sweep()  # anchors every dataset
        self.chunk_id = closed_step(self.hub, self.graph, 1)

    def sweep(self) -> None:
        sweep = self.hub.hub_services.egress_export
        assert sweep is not None
        self.hub.clock.advance(timedelta(seconds=1))
        sweep.sweep()

    def segment(self, segment_id: str, chunk_id: str | None = None) -> None:
        record = TranscriptSlice(
            segment_id=segment_id,
            chunk_id=chunk_id or self.chunk_id,
            node_id=graph_node_id(self.graph),
            epoch=1,
            spawn_generation=1,
            runner_id="r1",
            turn_range_start=0,
            turn_range_end=0,
            final=True,
            normalizer_version="claude-code-jsonl/2",
            harness_version="claude-code-1.0",
            record_truncated=False,
            turns_json=json.dumps([{"index": 0, "kind": "user", "text": _PLANTED}]),
            spawn_cwd="/work",
        )
        SeedingTranscriptSegmentStore(hub_store_connections(self.hub.engine)).insert_accepted(
            record, byte_count=1, at=self.hub.clock.now()
        )

    def derive(
        self, segment_id: str, events: int, *, version: str = EXTRACTOR_VERSION, chunk_id: str | None = None
    ) -> None:
        self.derive_events(
            segment_id, [self._event(turn, chunk_id or self.chunk_id) for turn in range(events)], version
        )

    def derive_events(self, segment_id: str, events: list[TranscriptEvent], version: str = EXTRACTOR_VERSION) -> None:
        self.hub.clock.advance(timedelta(seconds=1))
        TranscriptEventStore(hub_store_connections(self.hub.engine)).replace_segment_events(
            segment_id,
            version,
            events,
            complete=True,
            content_fingerprint="fp",
            at=self.hub.clock.now(),
            provenance=_PROVENANCE,
        )

    def drop(self, segment_id: str) -> None:
        self.hub.clock.advance(timedelta(seconds=1))
        TranscriptEventStore(hub_store_connections(self.hub.engine)).drop_segments(
            frozenset({segment_id}), at=self.hub.clock.now()
        )

    def reads_version(self, version: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Make the sweep read ``version`` as the hub's current extractor version, as an upgrade does."""
        sweep = self.hub.hub_services.egress_export
        assert sweep is not None
        monkeypatch.setattr(sweep, "_extractor_version", version)
        monkeypatch.setattr(sweep_module, "EXTRACTOR_VERSION", version)

    def view(self, name: str) -> list[tuple[str, ...]]:
        """The rows of the published view ``name`` over this directory, as ``(segment_id, extractor_version,
        kind, subject)`` in that order."""
        connection = duckdb.connect()
        listed = ", ".join(f"'{path}'" for path in self.files("events"))
        connection.execute(
            f"CREATE VIEW events AS SELECT * FROM read_json_auto([{listed}], format = 'newline_delimited', "
            "union_by_name = true, sample_size = -1)"
        )
        sql = (repo_root() / "contracts" / "egress" / f"{name}.sql").read_text()
        return connection.execute(
            f"SELECT segment_id, extractor_version, kind, subject FROM ({sql}) ORDER BY 1, 2, 3, 4"
        ).fetchall()

    def event(self, kind: str, turn: int, subject: str, occurrence: int = 0) -> TranscriptEvent:
        return replace(self._event(turn, self.chunk_id), kind=kind, subject=subject, occurrence=occurrence)

    def _event(self, turn: int, chunk_id: str) -> TranscriptEvent:
        return TranscriptEvent(
            kind="file_read",
            turn_path=str(turn),
            occurrence=0,
            payload=json.dumps({"input": _PLANTED}),
            subject=f"/work/src/f{turn}.py",
            tool="Read",
            chunk_id=chunk_id,
            node_id=graph_node_id(self.graph),
            epoch=1,
            spawn_generation=1,
            graph_id="gr_stamped_elsewhere",
            depth=0,
            agent_type=None,
            occurred_at=None,
        )

    def files(self, dataset: str) -> list[Path]:
        return sorted(self.directory.glob(f"{dataset}/v1/*/*.ndjson.gz"))

    def rows(self, dataset: str = "events") -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for path in self.files(dataset):
            with gzip.open(path, "rt") as handle:
                out += [json.loads(line) for line in handle.read().splitlines()]
        return out

    def manifests(self) -> list[dict[str, object]]:
        return [json.loads(path.read_text()) for path in sorted((self.directory / "_manifests").glob("*.json"))]


SCHEMA = DatasetSchema(
    "things",
    1,
    (
        ColumnSpec("id", ColumnType.STRING, False, "the id"),
        ColumnSpec("n", ColumnType.INT64, True, "a count"),
        ColumnSpec("ok", ColumnType.BOOL, True, "a flag"),
        ColumnSpec("at", ColumnType.TIMESTAMP, True, "a time"),
        ColumnSpec("cost", ColumnType.MONEY, True, "money"),
        ColumnSpec("tags", ColumnType.STRING_LIST, True, "labels"),
    ),
)


PASS = EgressPass(datetime(2026, 10, 1, 6, 15, tzinfo=UTC))


class RowFields(TypedDict, total=False):
    """The test schema's columns, plus a column it does not declare. Every value stays wide:
    the writer's own validation is what these tests feed malformed values to."""

    id: object
    n: object
    ok: object
    at: object
    cost: object
    tags: object
    extra: object


def row(position: str = "p1", /, **overrides: Unpack[RowFields]) -> EgressValues:
    values: RowFields = {
        "id": "a",
        "n": 1,
        "ok": True,
        "at": datetime(2026, 10, 1, tzinfo=UTC),
        "cost": Decimal("1.5"),
        "tags": ["x"],
    }
    values.update(overrides)
    return EgressValues(position, values)


def batch(*rows: EgressValues) -> EgressBatch:
    return EgressBatch(SCHEMA, date(2026, 10, 1), PASS, rows)
