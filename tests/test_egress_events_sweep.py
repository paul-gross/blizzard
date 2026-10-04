"""The ``events`` dataset through the live egress sweep (component tier) — derivations and drops written by the
hub's composed sweep to a real directory, partitioned by their step's start, with status and reset over it."""

from __future__ import annotations

import gzip
import json
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub import app as hub_app
from blizzard.hub.cli.egress import StatusView
from blizzard.hub.config import EgressConfig
from blizzard.hub.domain.observability.analytics.events import SegmentProvenance, TranscriptEvent
from blizzard.hub.domain.observability.analytics.extraction import EXTRACTOR_VERSION
from blizzard.hub.domain.observability.egress.event_rows import derivation_id
from blizzard.hub.domain.observability.egress.repository import EventsPosition
from blizzard.hub.domain.observability.transcripts import TranscriptSlice
from blizzard.hub.store import schema
from blizzard.hub.store.internal.egress_store import EgressStore
from blizzard.hub.store.internal.transcript_event_store import TranscriptEventStore
from blizzard.hub.store.internal.transcript_segment_store import TranscriptSegmentStore
from tests.support import HubHarness, hub_store_connections
from tests.test_egress_sweep import _closed_step, _kinds, _node_id
from tests.trace_hub import trace_hub

pytestmark = pytest.mark.component

_OLD_VERSION = "blizzard-analytics/0"
_PLANTED = "PLANTED-PROMPT-TEXT"
_PROVENANCE = SegmentProvenance("claude_code", "1.0", "claude-sonnet-5", "high")


def _config(tmp_path: Path, **extra: object) -> EgressConfig:
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    return EgressConfig(
        directory=tmp_path / "out",
        settle_seconds=0,
        min_free_bytes=0,
        **{"file_paths": "absolute", **extra},  # type: ignore[arg-type]
    )


class _Events:
    """A hub with the export on, a closed runner step, and that step's transcript segments, derived by hand."""

    def __init__(self, tmp_path: Path, *, path_key: bytes | None = None, **extra: object) -> None:
        self.directory = tmp_path / "out"
        self.hub, self.graph = trace_hub(tmp_path, egress=_config(tmp_path, **extra), egress_path_key=path_key)
        self.sweep()  # anchors every dataset
        self.chunk_id = _closed_step(self.hub, self.graph, 1)

    def sweep(self) -> None:
        sweep = self.hub.services.egress_export
        assert sweep is not None
        self.hub.clock.advance(timedelta(seconds=1))
        sweep.sweep()

    def segment(self, segment_id: str, chunk_id: str | None = None) -> None:
        record = TranscriptSlice(
            segment_id=segment_id,
            chunk_id=chunk_id or self.chunk_id,
            node_id=_node_id(self.graph),
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
        TranscriptSegmentStore(hub_store_connections(self.hub.engine)).insert_accepted(
            record, byte_count=1, codec="zlib", at=self.hub.clock.now()
        )

    def derive(
        self, segment_id: str, events: int, *, version: str = EXTRACTOR_VERSION, chunk_id: str | None = None
    ) -> None:
        self.hub.clock.advance(timedelta(seconds=1))
        TranscriptEventStore(hub_store_connections(self.hub.engine)).replace_segment_events(
            segment_id,
            version,
            [self._event(turn, chunk_id or self.chunk_id) for turn in range(events)],
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

    def _event(self, turn: int, chunk_id: str) -> TranscriptEvent:
        return TranscriptEvent(
            kind="file_read",
            turn_path=str(turn),
            occurrence=0,
            payload=json.dumps({"input": _PLANTED}),
            subject=f"/work/src/f{turn}.py",
            tool="Read",
            chunk_id=chunk_id,
            node_id=_node_id(self.graph),
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


def _derivations(rows: list[dict[str, object]]) -> list[tuple[object, ...]]:
    return [(r["segment_id"], r["event_count"]) for r in rows if r["record_type"] == "derivation"]


def test_re_derived_emptied_and_dropped_segments_each_write_their_record(tmp_path: Path) -> None:
    world = _Events(tmp_path)
    world.segment("sg_a")
    world.derive("sg_a", 2)
    world.sweep()
    first = world.rows()
    assert [r["record_type"] for r in first] == ["derivation", "event", "event"]

    world.derive("sg_a", 3)
    world.sweep()
    world.derive("sg_a", 0)
    world.sweep()
    world.drop("sg_a")
    world.sweep()

    rows = world.rows()
    assert _derivations(rows) == [("sg_a", 2), ("sg_a", 3), ("sg_a", 0)]
    derived = [str(r["derived_at"]) for r in rows if r["record_type"] == "derivation"]
    assert derived == sorted(derived) and len(set(derived)) == 3
    assert len({r["derivation_id"] for r in rows if r["record_type"] == "derivation"}) == 3
    [dropped] = [r for r in rows if r["record_type"] == "dropped"]
    assert dropped["segment_id"] == "sg_a"
    assert dropped["extractor_version"] is None
    assert len(world.files("events")) == 4


def test_a_batch_limit_inside_a_derivation_still_writes_it_whole(tmp_path: Path) -> None:
    world = _Events(tmp_path, batch_limit=2)
    world.segment("sg_a")
    world.segment("sg_b")
    world.derive("sg_a", 5)
    world.derive("sg_b", 0)

    world.sweep()
    assert [r["record_type"] for r in world.rows()] == ["derivation"] + ["event"] * 5

    world.sweep()
    assert _derivations(world.rows()) == [("sg_a", 5), ("sg_b", 0)]


def test_current_writes_only_the_hubs_extractor_version_and_all_writes_every_one(tmp_path: Path) -> None:
    for setting, expected in (("current", [EXTRACTOR_VERSION]), ("all", [_OLD_VERSION, EXTRACTOR_VERSION])):
        world = _Events(tmp_path / setting, extractor_versions=setting)
        world.segment("sg_a")
        world.derive("sg_a", 1, version=_OLD_VERSION)
        world.derive("sg_a", 1)
        world.sweep()
        versions = [r["extractor_version"] for r in world.rows() if r["record_type"] == "derivation"]
        assert versions == expected, setting


def test_re_derivations_and_drops_land_in_their_steps_start_partition(tmp_path: Path) -> None:
    world = _Events(tmp_path)
    step_day = world.hub.clock.now().date()
    world.segment("sg_a")
    world.segment("sg_b")
    world.hub.clock.advance(timedelta(days=2))
    world.derive("sg_a", 1)
    world.drop("sg_b")
    world.sweep()

    assert {path.parent.name for path in world.files("events")} == {f"date={step_day}"}
    rows = world.rows()
    assert {r["record_type"] for r in rows} == {"derivation", "event", "dropped"}
    assert {str(r["step_started_at"])[:10] for r in rows} == {str(step_day)}


def test_an_events_manifest_names_the_extractor_version_and_a_steps_manifest_does_not(tmp_path: Path) -> None:
    world = _Events(tmp_path)
    world.segment("sg_a")
    world.derive("sg_a", 1)
    world.sweep()

    by_dataset = {m["files"][0]["dataset"]: m for m in world.manifests()}  # type: ignore[index]
    assert by_dataset["events"]["extractor_version"] == EXTRACTOR_VERSION
    assert "extractor_version" not in by_dataset["steps"]
    assert "extractor_version" not in by_dataset["invocations"]


def test_an_event_names_its_step_rows_graph_and_no_row_carries_transcript_content(tmp_path: Path) -> None:
    world = _Events(tmp_path)
    world.segment("sg_a")
    world.derive("sg_a", 2)
    world.sweep()

    [step] = world.rows("steps")
    rows = world.rows()
    assert {(r["graph_id"], r["graph_name"], r["node_name"]) for r in rows} == {
        (step["graph_id"], step["graph_name"], step["node_name"])
    }
    assert {r["step_key"] for r in rows} == {step["step_key"]}
    derivation = rows[0]
    assert derivation["derivation_id"] == derivation_id(
        "sg_a", EXTRACTOR_VERSION, world.hub.clock.now() - timedelta(seconds=1)
    )
    assert [r["subject"] for r in rows[1:]] == ["/work/src/f0.py", "/work/src/f1.py"]
    text = "".join(gzip.decompress(path.read_bytes()).decode() for path in world.files("events"))
    assert _PLANTED not in text
    assert '"payload"' not in text


def test_status_reports_the_events_cursor_and_its_lag_and_reset_moves_it(tmp_path: Path) -> None:
    world = _Events(tmp_path)
    hub: HubHarness = world.hub
    world.segment("sg_a")
    world.derive("sg_a", 1)
    derived_at = hub.clock.now()
    hub.clock.advance(timedelta(seconds=30))

    waiting = {d["name"]: d for d in hub.client.get("/api/egress/status").json()["datasets"]}
    assert set(waiting) == {"steps", "invocations", "events"}
    assert waiting["events"]["lag_seconds"] == pytest.approx(30)

    world.sweep()
    caught_up = {d["name"]: d for d in hub.client.get("/api/egress/status").json()["datasets"]}
    assert caught_up["events"]["cursor_at"] == iso_utc(derived_at)
    assert caught_up["events"]["lag_seconds"] is None

    resp = hub.client.post("/api/egress/reset", json={"dataset": "events", "to": iso_utc(derived_at - timedelta(1))})
    assert resp.status_code == 200, resp.text
    assert resp.json()["direction"] == "repeated"
    moved = EgressStore(hub_store_connections(hub.engine)).newest_cursor("events")
    assert moved is not None
    assert moved.events == EventsPosition(derived_at - timedelta(1))
    world.sweep()
    assert _derivations(world.rows()) == [("sg_a", 1), ("sg_a", 1)]


@pytest.mark.parametrize("file_paths", ["relative", "hashed"])
def test_a_hashing_policy_without_its_key_keeps_exporting_everything_but_events(
    tmp_path: Path, file_paths: str
) -> None:
    world = _Events(tmp_path, file_paths=file_paths)
    hub = world.hub
    hub_app._announce_rejected_egress(_config(tmp_path, file_paths=file_paths), hub.services)
    world.segment("sg_a")
    world.derive("sg_a", 1)
    world.sweep()

    assert _kinds(hub) == ["egress-config-rejected"]
    with hub.engine.connect() as conn:
        detail = json.loads(conn.execute(sa.select(schema.event_log.c.detail)).scalar_one())
    assert detail == {"setting": "egress.path_key_env", "value": "BZ_EGRESS_PATH_KEY"}
    assert len(world.rows("steps")) == 1
    assert world.files("invocations")
    assert world.files("events") == []
    status = hub.client.get("/api/egress/status").json()
    assert (status["state"], status["rejected_setting"], status["rejected_value"]) == (
        "on",
        "egress.path_key_env",
        "BZ_EGRESS_PATH_KEY",
    )
    assert {d["name"] for d in status["datasets"]} == {"steps", "invocations"}
    assert "events: off — egress.path_key_env='BZ_EGRESS_PATH_KEY' names no key" in StatusView(status).lines()
    now = hub.clock.now()
    for path, body in (
        ("/api/egress/reset", {"dataset": "events", "to": iso_utc(now)}),
        (
            "/api/egress/backfill",
            {"dataset": "events", "since": iso_utc(now - timedelta(1)), "until": iso_utc(now), "dry_run": True},
        ),
    ):
        resp = hub.client.post(path, json=body)
        assert resp.status_code == 422, path
        assert "BZ_EGRESS_PATH_KEY" in resp.json()["detail"], path


def test_a_hashing_policy_with_its_key_exports_events_and_records_no_rejection(tmp_path: Path) -> None:
    world = _Events(tmp_path, file_paths="hashed", path_key=b"k" * 32)
    hub_app._announce_rejected_egress(_config(tmp_path, file_paths="hashed"), world.hub.services)
    world.segment("sg_a")
    world.derive("sg_a", 1)
    world.sweep()

    assert _kinds(world.hub) == []
    assert _derivations(world.rows()) == [("sg_a", 1)]
    assert world.hub.client.get("/api/egress/status").json()["rejected_setting"] is None
