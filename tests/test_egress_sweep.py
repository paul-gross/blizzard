"""The egress export sweep (component tier) — closed steps and usage driven through the hub's real routes,
written one ``sweep()`` at a time against a fixed clock, to the in-memory writer and to a real directory."""

from __future__ import annotations

import gzip
import itertools
import json
from collections.abc import Sequence
from dataclasses import fields
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi import FastAPI

from blizzard.foundation.lane_retry import BACKOFF_CAP
from blizzard.hub import app as hub_app
from blizzard.hub.app import Sweep
from blizzard.hub.config import EgressConfig
from blizzard.hub.domain.egress.repository import UsagePosition
from blizzard.hub.domain.egress.rows import InvocationRow, StepRow
from blizzard.hub.domain.egress.schema import INVOCATIONS_SCHEMA, STEPS_SCHEMA
from blizzard.hub.domain.egress.sweep import EgressSweep
from blizzard.hub.domain.graph import Graph
from blizzard.hub.egress.factory import EgressUnavailable
from blizzard.hub.egress.internal.ndjson import NdjsonEgressWriter
from blizzard.hub.egress.writer import (
    EgressBatch,
    EgressFailure,
    EgressFailureCause,
    EgressPass,
    EgressWriterSettings,
    FilesWritten,
    IEgressWriter,
    ManifestCommitted,
    PlacedFile,
)
from blizzard.hub.store import schema
from blizzard.hub.store.internal.egress_store import EgressStore
from blizzard.hub.store.internal.trace_store import TraceStore
from tests.support import (
    HubHarness,
    InMemoryEgressWriter,
    count_queries,
    count_rows_read,
    hub_store_connections,
    ingest,
)
from tests.trace_hub import claim, label, pass_build, trace_hub

pytestmark = pytest.mark.component

_SETTLED = EgressConfig(directory=Path("unused"), settle_seconds=0, sweep_seconds=60)
_LOW_DISK = EgressFailure(EgressFailureCause.LOW_DISK, "low", free_bytes=5, required_bytes=10)
_IO = EgressFailure(EgressFailureCause.IO_ERROR, "no such directory")
_SEQ = itertools.count(1000)


def _hub(tmp_path: Path, config: EgressConfig | None = None) -> tuple[HubHarness, Graph]:
    return trace_hub(tmp_path, egress=config) if config is not None else trace_hub(tmp_path)


def _lines(path: Path) -> list[str]:
    with gzip.open(path, "rt") as handle:
        return handle.read().splitlines()


def _app(hub: HubHarness) -> FastAPI:
    assert hub.app is not None
    return hub.app


def _store(hub: HubHarness) -> EgressStore:
    return EgressStore(hub_store_connections(hub.engine))


def _sweep(
    hub: HubHarness, writer: IEgressWriter, config: EgressConfig = _SETTLED, *, store: EgressStore | None = None
) -> EgressSweep:
    connections = hub_store_connections(hub.engine)
    return EgressSweep(
        steps=TraceStore(connections, graphs=hub.services.graphs, label=label),
        egress=store or EgressStore(connections),
        writer=writer,
        events=hub.services.event_log,
        clock=hub.clock,
        config=config,
    )


def _push_usage(hub: HubHarness, chunk_id: str, node_id: str, *, cost: float | None = 0.5) -> None:
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
    resp = hub.client.post(
        "/api/fleet/events",
        json={"runner_id": "r1", "facts": [{"seq": next(_SEQ), "kind": "usage.recorded", "payload": payload}]},
    )
    assert resp.status_code == 200, resp.text


def _node_id(graph: Graph, name: str = "build") -> str:
    return next(n.node_id for n in graph.nodes if n.name == name)


def _closed_step(hub: HubHarness, graph: Graph, ref: int, *, usage: int = 1) -> str:
    """A chunk claimed, billed ``usage`` times, and moved out of ``build`` — one closed runner step."""
    chunk_id = ingest(hub, [{"source": "default", "ref": str(ref)}])
    claim(hub, chunk_id, seq=next(_SEQ))
    for _ in range(usage):
        _push_usage(hub, chunk_id, _node_id(graph))
    hub.clock.advance(timedelta(seconds=5))
    pass_build(hub, chunk_id, graph)
    return chunk_id


def _kinds(hub: HubHarness) -> list[str]:
    with hub.engine.connect() as conn:
        rows = conn.execute(sa.select(schema.event_log.c.kind).order_by(schema.event_log.c.id)).all()
    return [r.kind for r in rows if r.kind.startswith("egress-")]


def _cursor_rows(hub: HubHarness) -> int:
    with hub.engine.connect() as conn:
        return conn.execute(sa.select(sa.func.count()).select_from(schema.egress_cursor)).scalar_one()


def _rows(writer: InMemoryEgressWriter, dataset: str) -> list[dict[str, object]]:
    return [dict(row.values) for b in writer.batches if b.schema.name == dataset for row in b.rows]


def _anchored(hub: HubHarness, writer: IEgressWriter, config: EgressConfig = _SETTLED) -> None:
    _sweep(hub, writer, config).sweep()


def test_no_directory_yields_no_sweep_and_writes_no_cursor_row(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path)
    _closed_step(hub, graph, 1)

    assert hub.services.egress_export is None
    assert hub.services.egress_unavailable is None
    assert [s.logger_name for s in Sweep.all(_app(hub)) if "egress" in s.logger_name] == []
    assert _cursor_rows(hub) == 0


def test_a_directory_yields_the_sweep_and_the_first_pass_writes_only_the_anchor(tmp_path: Path) -> None:
    config = EgressConfig(directory=tmp_path / "out", settle_seconds=300)
    hub, graph = _hub(tmp_path, config)
    _closed_step(hub, graph, 1)  # closed before the export was on
    assert [s.logger_name for s in Sweep.all(_app(hub)) if "egress" in s.logger_name] == ["blizzard.hub.egress"]
    writer = InMemoryEgressWriter()

    _sweep(hub, writer, config).sweep()

    anchor = hub.clock.now() - timedelta(seconds=300)
    steps = _store(hub).newest_cursor("steps")
    invocations = _store(hub).newest_cursor("invocations")
    assert steps is not None
    assert invocations is not None
    assert steps.step is not None
    assert steps.step.at == anchor
    assert steps.usage == invocations.usage == UsagePosition(anchor)
    assert invocations.step is None
    assert (steps.row_count, invocations.row_count) == (0, 0)
    assert writer.batches == []
    _sweep(hub, writer, config).sweep()
    assert writer.batches == []
    assert _cursor_rows(hub) == 2


def test_a_pass_writes_steps_and_invocations_and_advances_each_cursor_after_its_manifest(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path)
    seen: list[tuple[int, int]] = []

    class Watching(InMemoryEgressWriter):
        def commit_pass(
            self, egress_pass: EgressPass, placed: Sequence[PlacedFile]
        ) -> ManifestCommitted | EgressFailure:
            # The manifest is being written: this dataset's cursor has not moved yet.
            seen.append((_cursor_rows(hub), len(self.manifests)))
            return super().commit_pass(egress_pass, placed)

    writer = Watching()
    _anchored(hub, writer)
    chunk_id = _closed_step(hub, graph, 1, usage=2)

    _sweep(hub, writer).sweep()

    assert [r["chunk_id"] for r in _rows(writer, "steps")] == [chunk_id]
    assert [r["usage_id"] for r in _rows(writer, "invocations")] == [1, 2]
    step = _rows(writer, "steps")[0]
    assert step["invocations"] == 2
    assert step["cost_billed_usd"] == Decimal("1.000000000")
    # steps wrote first: invocations' commit already saw steps' cursor row, and neither had advanced before its own.
    assert seen == [(2, 0), (3, 1)]
    steps = _store(hub).newest_cursor("steps")
    assert steps is not None
    assert (steps.row_count, len(steps.files)) == (1, 2)
    assert steps.files[-1].startswith("_manifests/")
    invocations = _store(hub).newest_cursor("invocations")
    assert invocations is not None
    assert invocations.row_count == 2
    assert (
        invocations.usage == UsagePosition(hub.clock.now() - timedelta(seconds=0), 2) or invocations.usage.usage_id == 2
    )


def test_rows_land_in_the_partition_of_their_own_time(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path)
    writer = InMemoryEgressWriter()
    _anchored(hub, writer)
    first = _closed_step(hub, graph, 1)
    hub.clock.advance(timedelta(days=1))
    second = _closed_step(hub, graph, 2)

    _sweep(hub, writer).sweep()

    by_partition = {(b.schema.name, str(b.partition)): [r.values["chunk_id"] for r in b.rows] for b in writer.batches}
    assert by_partition[("steps", "2026-07-13")] == [first]
    assert by_partition[("steps", "2026-07-14")] == [second]
    assert {k for k in by_partition if k[0] == "invocations"} == {
        ("invocations", "2026-07-13"),
        ("invocations", "2026-07-14"),
    }


def test_a_row_inside_the_settle_window_waits_until_it_has_stood_the_full_window(tmp_path: Path) -> None:
    config = EgressConfig(directory=Path("unused"), settle_seconds=300)
    hub, graph = _hub(tmp_path)
    writer = InMemoryEgressWriter()
    _anchored(hub, writer, config)
    _closed_step(hub, graph, 1)  # usage recorded at +0s, the step closed at +5s

    hub.clock.advance(timedelta(seconds=294))
    _sweep(hub, writer, config).sweep()
    assert writer.batches == []

    hub.clock.advance(timedelta(seconds=5))
    _sweep(hub, writer, config).sweep()
    assert (len(_rows(writer, "steps")), len(_rows(writer, "invocations"))) == (0, 1)

    hub.clock.advance(timedelta(seconds=1))
    _sweep(hub, writer, config).sweep()
    assert len(_rows(writer, "steps")) == 1


def test_batch_limit_bounds_a_pass_and_later_passes_take_the_rest_in_order(tmp_path: Path) -> None:
    config = EgressConfig(directory=Path("unused"), settle_seconds=0, batch_limit=1)
    hub, graph = _hub(tmp_path)
    writer = InMemoryEgressWriter()
    _anchored(hub, writer, config)
    _closed_step(hub, graph, 1, usage=2)
    _closed_step(hub, graph, 2)

    for _ in range(6):
        _sweep(hub, writer, config).sweep()

    assert sorted(int(str(r["usage_id"])) for r in _rows(writer, "invocations")) == [1, 2, 3]
    assert len(_rows(writer, "steps")) == 2


def test_late_usage_writes_its_step_again_and_the_newest_copy_agrees_with_its_invocations(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path)
    writer = InMemoryEgressWriter()
    _anchored(hub, writer)
    chunk_id = _closed_step(hub, graph, 1)
    _sweep(hub, writer).sweep()
    assert [r["invocations"] for r in _rows(writer, "steps")] == [1]

    hub.clock.advance(timedelta(hours=3))
    _push_usage(hub, chunk_id, _node_id(graph), cost=0.25)  # forwarded hours late, for a step already written
    _sweep(hub, writer).sweep()

    copies = [r for r in _rows(writer, "steps") if r["chunk_id"] == chunk_id]
    assert [c["invocations"] for c in copies] == [1, 2]
    newest = max(copies, key=lambda c: c["exported_at"])  # type: ignore[arg-type,return-value]
    invoked = [r for r in _rows(writer, "invocations") if r["chunk_id"] == chunk_id]
    assert newest["invocations"] == len(invoked)
    for column in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_create_tokens"):
        assert newest[column] == sum(r[column] for r in invoked)  # type: ignore[misc]
    assert newest["cost_billed_usd"] == sum((r["cost_billed_usd"] for r in invoked), Decimal(0))  # type: ignore[misc]
    # The rewrite is partitioned by the step's own end, not by when the usage landed.
    assert {str(b.partition) for b in writer.batches if b.schema.name == "steps"} == {"2026-07-13"}


def test_usage_for_a_step_that_is_still_open_only_moves_the_position(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path)
    writer = InMemoryEgressWriter()
    _anchored(hub, writer)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    claim(hub, chunk_id, seq=next(_SEQ))
    _push_usage(hub, chunk_id, _node_id(graph))

    _sweep(hub, writer).sweep()

    assert _rows(writer, "steps") == []
    assert len(_rows(writer, "invocations")) == 1
    pending = _store(hub).newest_cursor("steps")
    assert pending is not None
    assert pending.usage.usage_id == 1
    hub.clock.advance(timedelta(seconds=5))
    pass_build(hub, chunk_id, graph)
    _sweep(hub, writer).sweep()
    assert [r["invocations"] for r in _rows(writer, "steps")] == [1]


def test_steps_read_usage_even_when_invocations_are_not_exported(tmp_path: Path) -> None:
    config = EgressConfig(directory=Path("unused"), settle_seconds=0, datasets=("steps",))
    hub, graph = _hub(tmp_path)
    writer = InMemoryEgressWriter()
    _anchored(hub, writer, config)
    chunk_id = _closed_step(hub, graph, 1)
    _sweep(hub, writer, config).sweep()
    hub.clock.advance(timedelta(minutes=1))
    _push_usage(hub, chunk_id, _node_id(graph))

    _sweep(hub, writer, config).sweep()

    assert [r["invocations"] for r in _rows(writer, "steps")] == [1, 2]
    assert _rows(writer, "invocations") == []
    assert _store(hub).newest_cursor("invocations") is None


@pytest.mark.parametrize("failure", [_IO, _LOW_DISK])
def test_a_failed_write_holds_the_cursor_backs_off_and_is_announced_once(
    tmp_path: Path, failure: EgressFailure
) -> None:
    hub, graph = _hub(tmp_path)
    writer = InMemoryEgressWriter()
    _anchored(hub, writer)
    start = _store(hub).newest_cursor("steps")
    _closed_step(hub, graph, 1)
    writer.fail = failure

    _sweep_once = _sweep(hub, writer)
    _sweep_once.sweep()
    assert _store(hub).newest_cursor("steps") == start
    assert _kinds(hub) == ["egress-write-failed"]
    with hub.engine.connect() as conn:
        detail = json.loads(conn.execute(sa.select(schema.event_log.c.detail)).scalar_one())
    assert detail["cause"] == failure.cause.value
    assert detail.get("free_bytes") == failure.free_bytes

    # One interval to the first retry, two to the next; a sweep in between writes nothing.
    calls = 0

    class Counting(InMemoryEgressWriter):
        def write(self, batch: EgressBatch) -> FilesWritten | EgressFailure:
            nonlocal calls
            calls += 1
            return super().write(batch)

    counting = Counting()
    counting.fail = failure
    retry = _sweep(hub, counting)
    retry.sweep()
    hub.clock.advance(timedelta(seconds=60))
    retry.sweep()
    first = calls
    hub.clock.advance(timedelta(seconds=60))
    retry.sweep()
    assert calls == first  # backing off
    hub.clock.advance(timedelta(seconds=60))
    retry.sweep()
    assert calls == first + 1
    assert _kinds(hub) == ["egress-write-failed"]
    assert _store(hub).newest_cursor("steps") == start

    counting.fail = None
    hub.clock.advance(timedelta(seconds=240))
    retry.sweep()
    assert len(_rows(counting, "steps")) == 1
    assert _kinds(hub) == ["egress-write-failed", "egress-write-recovered"]

    _closed_step(hub, graph, 10)
    retry.sweep()
    assert len(_rows(counting, "steps")) == 2
    assert _kinds(hub) == ["egress-write-failed", "egress-write-recovered"]


def test_the_backoff_stops_doubling_at_its_cap(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path)
    writer = InMemoryEgressWriter()
    _anchored(hub, writer)
    _closed_step(hub, graph, 1)
    writer.fail = _IO
    sweep = _sweep(hub, writer)
    for _ in range(8):
        hub.clock.advance(BACKOFF_CAP)
        sweep.sweep()
    writer.fail = None

    hub.clock.advance(BACKOFF_CAP - timedelta(seconds=1))
    sweep.sweep()
    assert writer.batches == []
    hub.clock.advance(timedelta(seconds=1))
    sweep.sweep()
    assert len(_rows(writer, "steps")) == 1


def test_a_restart_during_an_outage_does_not_announce_it_again(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path)
    writer = InMemoryEgressWriter()
    _anchored(hub, writer)
    _closed_step(hub, graph, 1)
    writer.fail = _IO
    _sweep(hub, writer).sweep()
    assert _kinds(hub) == ["egress-write-failed"]

    restarted = _sweep(hub, writer)  # a new process: no latch in memory
    hub.clock.advance(timedelta(seconds=60))
    restarted.sweep()
    assert _kinds(hub) == ["egress-write-failed"]

    writer.fail = None
    hub.clock.advance(timedelta(seconds=60))
    restarted.sweep()
    assert _kinds(hub) == ["egress-write-failed", "egress-write-recovered"]


def test_a_failure_in_the_second_dataset_keeps_the_first_datasets_cursor_advanced(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path)

    class FailsInvocations(InMemoryEgressWriter):
        def write(self, batch: EgressBatch) -> FilesWritten | EgressFailure:
            return _IO if batch.schema.name == "invocations" else super().write(batch)

    writer = FailsInvocations()
    _anchored(hub, writer)
    _closed_step(hub, graph, 1)

    _sweep(hub, writer).sweep()

    steps = _store(hub).newest_cursor("steps")
    invocations = _store(hub).newest_cursor("invocations")
    assert steps is not None
    assert steps.row_count == 1
    assert invocations is not None
    assert invocations.row_count == 0
    assert _kinds(hub) == ["egress-write-failed"]


def test_a_write_that_raises_is_a_failure_like_any_other(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path)

    class Raises(InMemoryEgressWriter):
        def write(self, batch: EgressBatch) -> FilesWritten | EgressFailure:
            raise OSError("disk detached")

    writer = Raises()
    _anchored(hub, writer)
    _closed_step(hub, graph, 1)
    _sweep(hub, writer).sweep()
    assert _kinds(hub) == ["egress-write-failed"]


def test_turning_the_export_off_and_on_resumes_from_the_cursor(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path)
    writer = InMemoryEgressWriter()
    _anchored(hub, writer)
    _closed_step(hub, graph, 1)
    _sweep(hub, writer).sweep()
    hub.clock.advance(timedelta(days=2))
    closed_while_off = _closed_step(hub, graph, 2)
    hub.clock.advance(timedelta(days=2))

    resumed = InMemoryEgressWriter()
    _sweep(hub, resumed).sweep()

    assert [r["chunk_id"] for r in _rows(resumed, "steps")] == [closed_while_off]
    assert _kinds(hub) == []


def test_a_crash_before_the_cursor_writes_the_same_rows_again_in_new_files(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path)
    directory = tmp_path / "out"
    directory.mkdir()
    settings = EgressWriterSettings(max_rows_per_file=100, min_free_bytes=0)
    store = _store(hub)
    first = NdjsonEgressWriter(directory, settings, "aaaaaa")
    _anchored(hub, first)
    chunk_id = _closed_step(hub, graph, 1)

    class Killed(Exception):
        pass

    class DiesBeforeCursor(EgressStore):
        def append_cursor(self, record) -> None:  # type: ignore[no-untyped-def]
            raise Killed

    with pytest.raises(Killed):
        _sweep(hub, first, store=DiesBeforeCursor(hub_store_connections(hub.engine))).sweep()
    placed_before = sorted(p.name for p in directory.rglob("*.ndjson.gz"))
    assert placed_before
    hub.clock.advance(timedelta(seconds=10))

    second = NdjsonEgressWriter(directory, settings, "bbbbbb")
    _sweep(hub, second, store=store).sweep()

    def rows(prefix: str) -> list[dict[str, object]]:
        out = []
        for path in sorted(directory.glob(f"{prefix}/v1/*/*.ndjson.gz")):
            out += [json.loads(line) for line in _lines(path)]
        return out

    copies = [r for r in rows("steps") if r["chunk_id"] == chunk_id]
    assert len(copies) == 2
    assert {k: v for k, v in copies[0].items() if k != "exported_at"} == {
        k: v for k, v in copies[1].items() if k != "exported_at"
    }
    step_files = sorted(p.name for p in directory.glob("steps/v1/*/*.ndjson.gz"))
    assert len(step_files) == 2 == len(set(step_files))
    assert placed_before == [step_files[0]]  # the first pass's file is untouched; the rewrite is a new name


def test_a_real_directory_holds_files_and_manifests_that_agree(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    config = EgressConfig(directory=tmp_path / "out", settle_seconds=0, min_free_bytes=0)
    hub, graph = _hub(tmp_path, config)
    sweep = hub.services.egress_export
    assert sweep is not None
    sweep.sweep()
    _closed_step(hub, graph, 1, usage=2)

    sweep.sweep()

    directory = tmp_path / "out"
    manifests = sorted((directory / "_manifests").glob("*.json"))
    assert len(manifests) == 2
    for manifest in manifests:
        document = json.loads(manifest.read_text())
        for entry in document["files"]:
            assert len(_lines(directory / entry["path"])) == entry["rows"]
    assert (directory / "_schema" / "steps.v1.json").exists()
    assert (directory / "_schema" / "invocations.v1.json").exists()
    names = {f["dataset"] for m in manifests for f in json.loads(m.read_text())["files"]}
    assert names == {"steps", "invocations"}


def test_a_missing_directory_fails_the_first_write_not_startup(tmp_path: Path) -> None:
    config = EgressConfig(directory=tmp_path / "never-made", settle_seconds=0, min_free_bytes=0)
    hub, graph = _hub(tmp_path, config)
    sweep = hub.services.egress_export
    assert sweep is not None
    sweep.sweep()
    _closed_step(hub, graph, 1)

    sweep.sweep()

    assert _kinds(hub) == ["egress-write-failed"]


def test_parquet_without_its_extra_keeps_the_hub_serving_and_records_one_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "blizzard.hub.composition.build_egress_writer", lambda *_args: EgressUnavailable("parquet needs pyarrow")
    )
    config = EgressConfig(directory=tmp_path / "out", format="parquet")
    hub, _graph = _hub(tmp_path, config)

    assert hub.services.egress_export is None
    assert [s for s in Sweep.all(_app(hub)) if "egress" in s.logger_name] == []
    hub_app._announce_rejected_egress(config, hub.services)
    assert _kinds(hub) == ["egress-config-rejected"]
    assert hub.client.get("/api/health").status_code == 200


@pytest.mark.parametrize(
    ("schema_", "row"),
    [(STEPS_SCHEMA, StepRow), (INVOCATIONS_SCHEMA, InvocationRow)],
    ids=["steps", "invocations"],
)
def test_each_dataset_schema_names_exactly_its_row_fields_in_order(schema_, row) -> None:  # type: ignore[no-untyped-def]
    assert [c.name for c in schema_.columns] == [f.name for f in fields(row)]
    assert all(c.meaning for c in schema_.columns)


def test_the_store_returns_usage_in_position_order_past_a_position_and_bounded_by_the_limit(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path)
    store = _store(hub)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    claim(hub, chunk_id, seq=next(_SEQ))
    for _ in range(4):
        _push_usage(hub, chunk_id, _node_id(graph))
        hub.clock.advance(timedelta(seconds=1))
    start = UsagePosition(hub.clock.now() - timedelta(days=1))

    assert [r.usage_id for r in store.usage_after(start, hub.clock.now(), 10)] == [1, 2, 3, 4]
    assert [r.usage_id for r in store.usage_after(start, hub.clock.now(), 2)] == [1, 2]
    second = store.usage_after(start, hub.clock.now(), 2)[1]
    after = UsagePosition(second.fact.recorded_at, second.usage_id)
    assert [r.usage_id for r in store.usage_after(after, hub.clock.now(), 10)] == [3, 4]
    until = store.usage_after(start, hub.clock.now(), 10)[2].fact.recorded_at
    assert [r.usage_id for r in store.usage_after(after, until, 10)] == [3]
    assert store.usage_after(UsagePosition(hub.clock.now()), hub.clock.now(), 10) == []


def test_the_store_answers_each_datasets_own_newest_cursor(tmp_path: Path) -> None:
    hub, _graph = _hub(tmp_path)
    store = _store(hub)
    assert store.newest_cursor("steps") is None
    writer = InMemoryEgressWriter()
    _anchored(hub, writer)
    hub.clock.advance(timedelta(seconds=30))
    _sweep(hub, writer).sweep()
    steps, invocations = store.newest_cursor("steps"), store.newest_cursor("invocations")
    assert steps is not None
    assert invocations is not None
    assert steps.step is not None
    assert invocations.step is None
    assert store.newest_egress_latch() is None


def test_an_idle_pass_reads_no_closing_candidates_however_many_steps_are_open(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path)
    writer = InMemoryEgressWriter()
    sweep = _sweep(hub, writer)
    sweep.sweep()
    _closed_step(hub, graph, 1)
    measured: list[tuple[int, int]] = []
    for batch in range(3):
        for ref in range(3):
            chunk_id = ingest(hub, [{"source": "default", "ref": str(100 + batch * 3 + ref)}])
            claim(hub, chunk_id, seq=next(_SEQ))
            _push_usage(hub, chunk_id, _node_id(graph))
        hub.clock.advance(timedelta(seconds=1))
        sweep.sweep()  # reads the new rows once and moves past them
        cursor_rows = _cursor_rows(hub)
        measured.append((count_queries(hub.engine, sweep.sweep), count_rows_read(hub.engine, sweep.sweep)))
        assert _cursor_rows(hub) == cursor_rows

    assert len(set(measured)) == 1
    assert len(_rows(writer, "steps")) == 1


def test_a_step_open_while_the_cursor_passed_is_written_once_when_it_closes(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path)
    writer = InMemoryEgressWriter()
    sweep = _sweep(hub, writer)
    sweep.sweep()
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    claim(hub, chunk_id, seq=next(_SEQ))
    _push_usage(hub, chunk_id, _node_id(graph))
    for _ in range(3):
        hub.clock.advance(timedelta(seconds=1))
        sweep.sweep()
    assert _rows(writer, "steps") == []

    pass_build(hub, chunk_id, graph)
    for _ in range(3):
        hub.clock.advance(timedelta(seconds=1))
        sweep.sweep()

    assert [r["chunk_id"] for r in _rows(writer, "steps")] == [chunk_id]
    assert len(_rows(writer, "invocations")) == 1
