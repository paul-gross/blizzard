"""A garden delivery stamps its facts under its lock (component tier): a state-preserving write that commits
between the delivery's validation and its lock leaves the guard unmoved, and is still older than every fact the
delivery writes."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from datetime import timedelta
from pathlib import Path

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.garden.delivery.materialize import (
    DeliveryOutcome,
    GardenDelivery,
    IWriteGardenDeliveryRepository,
)
from blizzard.hub.domain.garden.delivery.service import GardenDeliveryRecorder
from blizzard.hub.domain.garden.delivery.validation import IProjectCommitResolvers
from blizzard.hub.domain.garden.findings.bucket import FindingBucketReader
from blizzard.hub.domain.garden.findings.model import IWriteFindingRepository, IWriteFindingSetRepository
from blizzard.hub.domain.garden.formats import IGardenFormats
from blizzard.hub.domain.garden.proposals.model import IWriteGardenProposalRepository
from blizzard.hub.domain.garden.run_context import IReadRunContextRepository
from blizzard.hub.domain.graph.model import IReadGraphRepository
from blizzard.hub.domain.kernel.transactions import IHubTransactions, Transaction
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from tests.support.finding_units import seed_fact
from tests.support.garden_delivery_world import (
    _ROUTINE,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.garden_delivery_world
    SCOPE,
    garden_delta,
    record_artifact,
    seed_chunk,
    seed_scope,
)
from tests.support.scaffolding import HubHarness, build_hub, default_project, hub_store_connections


class _WinnerFirst:
    """Transactions whose first unit runs ``winner`` before it opens — a write landing between the
    delivery's validation and its lock."""

    def __init__(self, inner: IHubTransactions, winner: Callable[[], None]) -> None:
        self._inner = inner
        self._winner: Callable[[], None] | None = winner

    def begin(self) -> AbstractContextManager[Transaction]:
        return self._begin()

    @contextmanager
    def _begin(self) -> Generator[Transaction]:
        if self._winner is not None:
            winner, self._winner = self._winner, None
            winner()
        with self._inner.begin() as unit:
            yield unit


def _recorder(hub: HubHarness, transactions: IHubTransactions) -> GardenDeliveryRecorder:
    materialize = GardenDelivery(
        delivery=hub.default_adapter(IWriteGardenDeliveryRepository),
        findings=hub.default_adapter(IWriteFindingRepository),
        finding_sets=hub.default_adapter(IWriteFindingSetRepository),
        proposals=hub.default_adapter(IWriteGardenProposalRepository),
        facts=hub.default_adapter(IReadChunkFactsRepository),
        transactions=transactions,
        clock=hub.default_adapter(IClock),
    )
    return GardenDeliveryRecorder(
        artifacts=hub.default_adapter(IReadChunkArtifactsRepository),
        buckets=hub.default_adapter(FindingBucketReader),
        materialize=materialize,
        formats=hub.default_adapter(IGardenFormats),
        commit_resolvers=hub.default_adapter(IProjectCommitResolvers),
    )


def test_a_write_landing_before_the_lock_is_older_than_the_deliverys_facts(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    connections = hub_store_connections(hub.engine)
    finding_id = Id.mint(IdPrefix.FINDING, hub.clock).value
    FindingStore(connections).add(
        finding_id,
        project_id=default_project(hub.engine),
        routine_name=_ROUTINE,
        scope_slug=SCOPE,
        class_="stale-docstring",
        locus="a.py:1",
        summary="s",
        introduced=None,
        at=hub.clock.now() - timedelta(days=1),
    )
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[{"op": "observed", "id": finding_id}]))
    winner_at = hub.clock.now() + timedelta(minutes=1)

    def observed_elsewhere() -> None:
        seed_fact(connections, finding_id, kind="observed", at=winner_at)
        hub.clock.advance(timedelta(minutes=2))

    chunk = hub.default_adapter(IReadChunkRecordRepository).get(chunk_id)
    assert chunk is not None
    graph = hub.default_adapter(IReadGraphRepository).get(chunk.graph_id)
    assert graph is not None
    node = graph.node_by_id("nd_deliver")
    assert node is not None
    run = hub.default_adapter(IReadRunContextRepository).for_chunk(chunk)
    assert run is not None
    recorder = _recorder(hub, _WinnerFirst(hub.default_adapter(IHubTransactions), observed_elsewhere))

    outcome = recorder.record(chunk=chunk, node=node, epoch=1, run=run, delta_names=["delta"], proposal_names=[])

    assert outcome is DeliveryOutcome.RECORDED
    with hub.engine.connect() as conn:
        rows = conn.execute(
            connections.select(s.finding_facts, s.finding_facts.c.kind, s.finding_facts.c.recorded_at)
            .where(s.finding_facts.c.finding_id == finding_id)
            .order_by(s.finding_facts.c.id)
        ).all()
    assert [r.kind for r in rows] == ["add", "observed", "observed"]
    assert rows[2].recorded_at > rows[1].recorded_at == winner_at
