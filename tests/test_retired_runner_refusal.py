"""A retired runner is refused inside each guarded domain operation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
import sqlalchemy as sa

from blizzard.hub.api import transcripts as transcripts_api
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts
from blizzard.hub.domain.graph.model import Graph
from blizzard.hub.domain.observability.transcripts import TranscriptSlice
from blizzard.hub.domain.runners.registration import IWriteRunnerRegistry, RunnerRegistration, RunnerRetired
from blizzard.hub.domain.runners.route import Route
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.facts import RunnerFact, RunnerFactBatch
from blizzard.wire.transcript_segment import TranscriptSegmentRecord
from tests.support import HubHarness, build_hub, make_ready, report_lease

pytestmark = pytest.mark.component

_RUNNER = "runner-a"


def _retired_hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path)
    hub.services.fleet.register(_RUNNER, "ws-a")
    writer = cast(IWriteRunnerRegistry, hub.services.registry)
    writer.record_lifecycle(_RUNNER, retired=True, at=hub.clock.now(), by="op")
    return hub


def _registration(hub: HubHarness) -> RunnerRegistration:
    registration = hub.services.registry.get_runner(_RUNNER)
    assert registration is not None
    return registration


def test_register_refuses_a_retired_runner_before_any_write(tmp_path: Path) -> None:
    hub = _retired_hub(tmp_path)
    before = _registration(hub)

    with pytest.raises(RunnerRetired):
        hub.services.fleet.register(_RUNNER, "ws-b", env_capacity=9)

    after = _registration(hub)
    assert after.workspace_id == before.workspace_id
    assert after.env_capacity == before.env_capacity


def test_heartbeat_refuses_a_retired_runner_without_refreshing_liveness(tmp_path: Path) -> None:
    hub = _retired_hub(tmp_path)
    before = _registration(hub).last_seen_at
    hub.clock.advance(timedelta(seconds=60))

    with pytest.raises(RunnerRetired):
        hub.services.fleet.heartbeat(_RUNNER)

    assert _registration(hub).last_seen_at == before


def test_heartbeat_on_an_unregistered_runner_is_false_not_a_refusal(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.services.fleet.heartbeat("ghost") is False


def test_enroll_refuses_a_retired_runner_without_setting_a_token(tmp_path: Path) -> None:
    hub = _retired_hub(tmp_path)

    with pytest.raises(RunnerRetired, match="reinstate"):
        hub.services.enrollment.enroll(_registration(hub))

    assert _registration(hub).token_hash is None


def test_claim_refuses_a_retired_runner_and_leaves_the_chunk_unrouted(tmp_path: Path) -> None:
    hub = _retired_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    make_ready(hub, chunk_id)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    graph = hub.services.graphs.get(chunk.graph_id)
    assert graph is not None

    with pytest.raises(RunnerRetired):
        hub.services.claim.claim(chunk, graph, runner_id=_RUNNER, workspace_id="ws-a", environment_ids=["e1"])

    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["route"] is None


def _store_rows(hub: HubHarness) -> dict[str, list[str]]:
    metadata = sa.MetaData()
    metadata.reflect(hub.engine)
    with hub.engine.connect() as conn:
        return {
            name: sorted(repr(row) for row in conn.execute(sa.select(table)).all())
            for name, table in metadata.tables.items()
        }


class _Routed:
    """A chunk ``runner-a`` claimed and leased before it was retired."""

    def __init__(self, tmp_path: Path) -> None:
        self.hub = build_hub(tmp_path)
        self.hub.services.fleet.register(_RUNNER, "ws-a")
        self.chunk_id = self.hub.client.post("/api/chunks", json={"tokens": ["default:1"]}).json()["chunk_id"]
        assert self.hub.client.post(f"/api/chunks/{self.chunk_id}/promote").status_code == 202
        make_ready(self.hub, self.chunk_id)
        claim = self.hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": self.chunk_id, "runner_id": _RUNNER, "workspace_id": "ws-a", "environment_ids": ["e1"]},
        )
        assert claim.status_code == 201, claim.text
        report_lease(self.hub, self.chunk_id, epoch=1, seq=1, runner_id=_RUNNER)
        writer = cast(IWriteRunnerRegistry, self.hub.services.registry)
        writer.record_lifecycle(_RUNNER, retired=True, at=self.hub.clock.now(), by="op")
        self.hub.clock.advance(timedelta(seconds=5))

    @property
    def chunk(self) -> Chunk:
        chunk = self.hub.services.chunks.record.get(self.chunk_id)
        assert chunk is not None
        return chunk

    @property
    def graph(self) -> Graph:
        graph = self.hub.services.graphs.get(self.chunk.graph_id)
        assert graph is not None
        return graph

    @property
    def node_id(self) -> str:
        return str(self.hub.client.get(f"/api/chunks/{self.chunk_id}").json()["current_node_id"])

    @property
    def route(self) -> Route:
        route = self.hub.services.chunks.route.route_of(self.chunk_id)
        assert route is not None
        return route

    def transcript_record(self) -> tuple[int, TranscriptSlice]:
        wire = TranscriptSegmentRecord(
            seq=1,
            segment_id="sg_1",
            chunk_id=self.chunk_id,
            node_id=self.node_id,
            epoch=1,
            spawn_generation=1,
            turn_range_start=0,
            turn_range_end=0,
            final=True,
            normalizer_version="claude-code-jsonl/2",
            harness_version="claude-code-1.0",
            turns=[],
        )
        return wire.seq, transcripts_api.to_domain_record(wire, runner_id=_RUNNER)


_GUARDED: dict[str, Callable[[_Routed], object]] = {
    "fact ingest": lambda r: r.hub.services.facts.ingest(
        RunnerFactBatch(
            runner_id=_RUNNER,
            facts=[RunnerFact(seq=2, kind="lease.minted", payload={"chunk_id": r.chunk_id, "epoch": 2})],
        )
    ),
    "transcript ingest": lambda r: r.hub.services.transcript_ingest.ingest(_RUNNER, [r.transcript_record()]),
    "lease report": lambda r: r.hub.services.runner_facts.record_lease_minted(r.chunk, epoch=2, runner_id=_RUNNER),
    "escalation report": lambda r: r.hub.services.runner_facts.record_escalation(
        r.chunk, runner_id=_RUNNER, epoch=1, takeover_command="claude --resume"
    ),
    "completion": lambda r: r.hub.services.apply.apply(
        r.chunk,
        r.graph,
        CompletionSubmission(choice="pass", epoch=1, runner_id=_RUNNER, from_node_id=r.node_id, artifacts=[]),
    ),
    "decision": lambda r: r.hub.services.decisions.submit(
        r.chunk, r.graph, DecisionSubmission(from_node_id=r.node_id, epoch=1, runner_id=_RUNNER)
    ),
    "route-token rekey": lambda r: r.hub.services.claim.rekey(
        r.route, ChunkFacts.or_default(r.hub.services.chunks.facts.load_facts(r.chunk_id))
    ),
    "runner read": lambda r: r.hub.services.fleet.own_liveness(_registration(r.hub)),
}


@pytest.mark.parametrize("action", sorted(_GUARDED))
def test_each_runner_contact_operation_refuses_a_retired_runner_and_lands_nothing(tmp_path: Path, action: str) -> None:
    routed = _Routed(tmp_path)
    operation = _GUARDED[action]
    before = _store_rows(routed.hub)

    with pytest.raises(RunnerRetired, match=action):
        operation(routed)

    assert _store_rows(routed.hub) == before


def test_the_operator_read_still_shows_a_retired_runner(tmp_path: Path) -> None:
    hub = _retired_hub(tmp_path)

    assert hub.services.fleet.get_liveness(_registration(hub)).registration.retired is True
