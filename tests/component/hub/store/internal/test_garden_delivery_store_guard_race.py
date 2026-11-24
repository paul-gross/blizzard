"""A garden delivery's finding-state guard, and supersede's absorber guard. A person's verb that lands between a
write's read and its write must be judged first, as if it had landed before the read: a run never revives an exit, and a
supersede never names a non-live absorber."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.garden.delivery.materialize import (
    DeliveryOutcome,
    DeliveryPlan,
    DeltaMaterialization,
    NewFindingFact,
    NewFindingSet,
)
from blizzard.hub.domain.garden.findings.model import (
    AbsorberNotLive,
    FindingExitService,
)
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.domain.kernel.transactions import IHubTransactions, Transaction, TransactionMisuse
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.schema import artifacts, finding_facts
from blizzard.hub.store.transactions import HubTransactions
from tests.support.delivery_writes import GardenDeliveryWrites
from tests.support.finding_units import delivery_unit, seed_fact
from tests.support.scaffolding import (
    chunk_unit,
    default_project,
    default_tenant,
    hub_store_connections,
    migrated_hub_at,
    seed_chunk,
    seed_graph,
)

_PROJECT = "proj_test"

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
_LATER = _NOW.replace(hour=13)
_RUN = RunContext(project_id=_PROJECT, routine_name="nightly", scope_slug="blizzard", mode="full")


def _world(tmp_path: Path) -> tuple[FindingStore, GardenDeliveryWrites, Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migrated_hub_at(db_url)
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO scopes (scope_id, slug, description, created_at, tenant_id, project_id)"
                " VALUES ('scp_blizzard', 'blizzard', '', :now, :tenant, :project)"
            ),
            {"now": _NOW, "tenant": default_tenant(engine), "project": default_project(engine)},
        )
        seed_graph(conn, "gr_1", at=_NOW)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_NOW)
    connections = hub_store_connections(engine)
    return FindingStore(connections), GardenDeliveryWrites(connections), engine


def _add_finding(findings: FindingStore, project_id: str, finding_id: str) -> None:
    findings.add(
        finding_id,
        project_id=project_id,
        routine_name="nightly",
        scope_slug="blizzard",
        class_="stale-docstring",
        locus="a.py:1",
        summary="s",
        introduced=None,
        at=_NOW,
    )


def _plan(project_id: str, *facts: NewFindingFact, expect: dict[str, str]) -> DeliveryPlan:
    return DeliveryPlan(
        chunk_id="ch_1",
        node_id="nd_1",
        node_name="garden-deliver",
        epoch=1,
        at=_LATER,
        run=replace(_RUN, project_id=project_id),
        deltas=[
            DeltaMaterialization(
                finding_set=NewFindingSet(
                    finding_set_id="fins_1",
                    artifact_id="art_1",
                    scope_slug="blizzard",
                    revisions={},
                    measurement=None,
                ),
                facts=list(facts),
            )
        ],
        proposals=[],
        expect=expect,
    )


def _kinds(engine: Engine, finding_id: str) -> list[str]:
    with engine.connect() as conn:
        return [
            r.kind
            for r in conn.execute(
                hub_store_connections(engine)
                .select(finding_facts, finding_facts.c.kind)
                .where(finding_facts.c.finding_id == finding_id)
                .order_by(finding_facts.c.id)
            )
        ]


def _markers(engine: Engine) -> int:
    with engine.connect() as conn:
        markers = hub_store_connections(engine).select(artifacts, artifacts.c.artifact_id)
        return len(conn.execute(markers.where(artifacts.c.name == "garden-delivered")).all())


def test_deliver_refuses_a_unit_not_holding_every_expected_finding_key(tmp_path: Path) -> None:
    findings, deliveries, engine = _world(tmp_path)
    project = default_project(engine)
    _add_finding(findings, project, "fin_1")
    plan = _plan(
        project, NewFindingFact(finding_id="fin_1", kind="observed", finding_set_id="fins_1"), expect={"fin_1": "live"}
    )

    with pytest.raises(TransactionMisuse), chunk_unit(hub_store_connections(engine), plan.chunk_id) as unit:
        deliveries.deliver(plan, admission=EpochAdmission.AT_OR_ABOVE, terminal=False, transaction=unit)

    assert _kinds(engine, "fin_1") == ["add"]
    assert _markers(engine) == 0


def test_deliver_loses_the_guard_when_an_exit_landed_after_validation(tmp_path: Path) -> None:
    findings, deliveries, engine = _world(tmp_path)
    project = default_project(engine)
    _add_finding(findings, project, "fin_1")
    plan = _plan(
        project, NewFindingFact(finding_id="fin_1", kind="observed", finding_set_id="fins_1"), expect={"fin_1": "live"}
    )
    seed_fact(hub_store_connections(engine), "fin_1", kind="wont-fix", at=_LATER, note="n", actor="u1")

    with delivery_unit(hub_store_connections(engine), plan) as unit:
        outcome = deliveries.deliver(plan, admission=EpochAdmission.AT_OR_ABOVE, terminal=False, transaction=unit)

    assert outcome is DeliveryOutcome.GUARD_LOST
    assert _kinds(engine, "fin_1") == ["add", "wont-fix"]
    assert _markers(engine) == 0


def test_deliver_records_when_the_guarded_finding_has_not_moved(tmp_path: Path) -> None:
    findings, deliveries, engine = _world(tmp_path)
    project = default_project(engine)
    _add_finding(findings, project, "fin_1")
    plan = _plan(
        project, NewFindingFact(finding_id="fin_1", kind="observed", finding_set_id="fins_1"), expect={"fin_1": "live"}
    )

    with delivery_unit(hub_store_connections(engine), plan) as unit:
        outcome = deliveries.deliver(plan, admission=EpochAdmission.AT_OR_ABOVE, terminal=False, transaction=unit)

    assert outcome is DeliveryOutcome.RECORDED
    assert _kinds(engine, "fin_1") == ["add", "observed"]
    assert _markers(engine) == 1


def test_a_reopened_delivered_finding_lands_the_runs_gone_as_gone(tmp_path: Path) -> None:
    """Validation judged `fin_1` `delivered` and planned `resolved`; a person reopens it before the
    write. The stale plan loses the guard, and a re-plan against the fresh state flags it `gone`."""
    findings, deliveries, engine = _world(tmp_path)
    project = default_project(engine)
    _add_finding(findings, project, "fin_1")
    seed_fact(hub_store_connections(engine), "fin_1", kind="delivered", at=_NOW, note="n", actor="u1")
    stale = _plan(
        project,
        NewFindingFact(finding_id="fin_1", kind="resolved", finding_set_id="fins_1", note="gone", actor="u1"),
        expect={"fin_1": "delivered"},
    )
    seed_fact(hub_store_connections(engine), "fin_1", kind="reopened", at=_LATER, note="n", actor="u2")

    with delivery_unit(hub_store_connections(engine), stale) as unit:
        assert (
            deliveries.deliver(stale, admission=EpochAdmission.AT_OR_ABOVE, terminal=False, transaction=unit)
            is DeliveryOutcome.GUARD_LOST
        )
    fresh = _plan(
        project,
        NewFindingFact(finding_id="fin_1", kind="gone", finding_set_id="fins_1", note="gone"),
        expect={"fin_1": "live"},
    )
    with delivery_unit(hub_store_connections(engine), fresh) as unit:
        assert (
            deliveries.deliver(fresh, admission=EpochAdmission.AT_OR_ABOVE, terminal=False, transaction=unit)
            is DeliveryOutcome.RECORDED
        )
    assert _kinds(engine, "fin_1")[-1] == "gone"


def test_deliver_ignores_a_moved_finding_whose_facts_are_not_inserted(tmp_path: Path) -> None:
    """A delta already materialized under an earlier visit is dropped, so its ops write nothing and
    a finding they name having moved is no reason to refuse."""
    findings, deliveries, engine = _world(tmp_path)
    project = default_project(engine)
    _add_finding(findings, project, "fin_1")
    plan = _plan(
        project, NewFindingFact(finding_id="fin_1", kind="observed", finding_set_id="fins_1"), expect={"fin_1": "live"}
    )
    with delivery_unit(hub_store_connections(engine), plan) as unit:
        assert (
            deliveries.deliver(plan, admission=EpochAdmission.AT_OR_ABOVE, terminal=False, transaction=unit)
            is DeliveryOutcome.RECORDED
        )
    seed_fact(hub_store_connections(engine), "fin_1", kind="wont-fix", at=_LATER, note="n", actor="u1")
    revisit = replace(plan, node_id="nd_2", epoch=2)

    with delivery_unit(hub_store_connections(engine), revisit) as unit:
        outcome = deliveries.deliver(revisit, admission=EpochAdmission.AT_OR_ABOVE, terminal=False, transaction=unit)

    assert outcome is DeliveryOutcome.ALREADY_RECORDED
    assert _kinds(engine, "fin_1") == ["add", "observed", "wont-fix"]


def test_supersede_refuses_an_absorber_exited_after_it_was_read(tmp_path: Path) -> None:
    findings, _, engine = _world(tmp_path)
    project = default_project(engine)
    _add_finding(findings, project, "fin_a")
    _add_finding(findings, project, "fin_b")
    absorber = findings.get("fin_b")
    assert absorber is not None
    superseded = findings.get("fin_a")
    assert superseded is not None
    seed_fact(hub_store_connections(engine), "fin_b", kind="wont-fix", at=_LATER, note="n", actor="u2")
    service = FindingExitService(
        repo=findings, clock=FixedClock(instant=_LATER), transactions=HubTransactions(hub_store_connections(engine))
    )

    with pytest.raises(AbsorberNotLive):
        service.supersede([superseded], absorber, note="dup", actor="u1")

    assert _kinds(engine, "fin_a") == ["add"]


def test_crossing_supersedes_land_exactly_one(tmp_path: Path) -> None:
    """A→B and B→A, each having read the other as live: whichever runs second finds its absorber
    moved and is refused, so no cycle forms."""
    findings, _, engine = _world(tmp_path)
    project = default_project(engine)
    _add_finding(findings, project, "fin_a")
    _add_finding(findings, project, "fin_b")
    a, b = findings.get("fin_a"), findings.get("fin_b")
    assert a is not None and b is not None
    service = FindingExitService(
        repo=findings, clock=FixedClock(instant=_LATER), transactions=HubTransactions(hub_store_connections(engine))
    )

    service.supersede([a], b, note="dup", actor="u1")
    with pytest.raises(AbsorberNotLive):
        service.supersede([b], a, note="dup", actor="u2")

    assert _kinds(engine, "fin_a") == ["add", "superseded"]
    assert _kinds(engine, "fin_b") == ["add"]


class _WinnerFirst:
    """Transactions whose first unit runs ``winner`` before it opens — a write that commits between the
    loser's read and its first lock."""

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


def test_a_verb_retried_after_a_later_winner_is_stamped_newest(tmp_path: Path) -> None:
    """`fin_1` is gone; P1's resolve reads it, P2's reopen commits at a later instant inside P1's first
    attempt. P1 loses the guard, retries on the reopened finding, and its `resolved` is the newest fact."""
    findings, _, engine = _world(tmp_path)
    connections = hub_store_connections(engine)
    _add_finding(findings, default_project(engine), "fin_1")
    seed_fact(connections, "fin_1", kind="gone", at=_NOW, note="fixed")
    loser = findings.get("fin_1")
    assert loser is not None and loser.state == "gone"
    clock = FixedClock(instant=_NOW + timedelta(minutes=1))
    winner_at = _NOW + timedelta(minutes=2)

    def reopen() -> None:
        seed_fact(connections, "fin_1", kind="reopened", at=winner_at, note="regressed", actor="u2")
        clock.advance(timedelta(minutes=2))

    service = FindingExitService(
        repo=findings, clock=clock, transactions=_WinnerFirst(HubTransactions(connections), reopen)
    )

    service.resolve([loser], note="landed", actor="u1")

    assert _kinds(engine, "fin_1") == ["add", "gone", "reopened", "resolved"]
    with engine.connect() as conn:
        newest = conn.execute(
            connections.select(finding_facts, finding_facts.c.kind)
            .where(finding_facts.c.finding_id == "fin_1")
            .order_by(finding_facts.c.recorded_at.desc(), finding_facts.c.id.desc())
            .limit(1)
        ).scalar_one()
    assert newest == "resolved"
    resolved = findings.get("fin_1")
    assert resolved is not None and resolved.state == "resolved"
