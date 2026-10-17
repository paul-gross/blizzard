"""A garden delivery's finding-state guard, and supersede's absorber guard (component and
unit tiers). A person's verb that lands between a write's read and its write must be
judged first, as if it had landed before the read: a run never revives an exit, and a
supersede never names a non-live absorber."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.garden.delivery.materialize import (
    DeliveryOutcome,
    DeliveryPlan,
    DeltaMaterialization,
    GardenDelivery,
    NewFindingFact,
    NewFindingSet,
)
from blizzard.hub.domain.garden.delivery.service import GardenDeliveryRecorder
from blizzard.hub.domain.garden.delivery.validation import GardenDeliveryRejected, ValidatedDelivery
from blizzard.hub.domain.garden.findings.bucket import FindingBucket
from blizzard.hub.domain.garden.findings.model import (
    GUARD_ATTEMPTS,
    AbsorberNotLive,
    FindingExitService,
)
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.domain.graph.model import Node
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.garden_delivery_store import GardenDeliveryStore
from blizzard.hub.store.schema import artifacts, finding_facts
from tests.support import (
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


def _world(tmp_path: Path) -> tuple[FindingStore, GardenDeliveryStore, Engine]:
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
    return FindingStore(connections), GardenDeliveryStore(connections), engine


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


@pytest.mark.component
def test_deliver_loses_the_guard_when_an_exit_landed_after_validation(tmp_path: Path) -> None:
    findings, deliveries, engine = _world(tmp_path)
    project = default_project(engine)
    _add_finding(findings, project, "fin_1")
    plan = _plan(
        project, NewFindingFact(finding_id="fin_1", kind="observed", finding_set_id="fins_1"), expect={"fin_1": "live"}
    )
    findings.record_fact("fin_1", kind="wont-fix", at=_LATER, note="n", actor="u1")

    outcome = deliveries.deliver(plan, admission=EpochAdmission.AT_OR_ABOVE)

    assert outcome is DeliveryOutcome.GUARD_LOST
    assert _kinds(engine, "fin_1") == ["add", "wont-fix"]
    assert _markers(engine) == 0


@pytest.mark.component
def test_deliver_records_when_the_guarded_finding_has_not_moved(tmp_path: Path) -> None:
    findings, deliveries, engine = _world(tmp_path)
    project = default_project(engine)
    _add_finding(findings, project, "fin_1")
    plan = _plan(
        project, NewFindingFact(finding_id="fin_1", kind="observed", finding_set_id="fins_1"), expect={"fin_1": "live"}
    )

    outcome = deliveries.deliver(plan, admission=EpochAdmission.AT_OR_ABOVE)

    assert outcome is DeliveryOutcome.RECORDED
    assert _kinds(engine, "fin_1") == ["add", "observed"]
    assert _markers(engine) == 1


@pytest.mark.component
def test_a_reopened_delivered_finding_lands_the_runs_gone_as_gone(tmp_path: Path) -> None:
    """Validation judged `fin_1` `delivered` and planned `resolved`; a person reopens it before the
    write. The stale plan loses the guard, and a re-plan against the fresh state flags it `gone`."""
    findings, deliveries, engine = _world(tmp_path)
    project = default_project(engine)
    _add_finding(findings, project, "fin_1")
    findings.record_fact("fin_1", kind="delivered", at=_NOW, note="n", actor="u1")
    stale = _plan(
        project,
        NewFindingFact(finding_id="fin_1", kind="resolved", finding_set_id="fins_1", note="gone", actor="u1"),
        expect={"fin_1": "delivered"},
    )
    findings.record_fact("fin_1", kind="reopened", at=_LATER, note="n", actor="u2")

    assert deliveries.deliver(stale, admission=EpochAdmission.AT_OR_ABOVE) is DeliveryOutcome.GUARD_LOST
    fresh = _plan(
        project,
        NewFindingFact(finding_id="fin_1", kind="gone", finding_set_id="fins_1", note="gone"),
        expect={"fin_1": "live"},
    )
    assert deliveries.deliver(fresh, admission=EpochAdmission.AT_OR_ABOVE) is DeliveryOutcome.RECORDED
    assert _kinds(engine, "fin_1")[-1] == "gone"


@pytest.mark.component
def test_deliver_ignores_a_moved_finding_whose_facts_are_not_inserted(tmp_path: Path) -> None:
    """A delta already materialized under an earlier visit is dropped, so its ops write nothing and
    a finding they name having moved is no reason to refuse."""
    findings, deliveries, engine = _world(tmp_path)
    project = default_project(engine)
    _add_finding(findings, project, "fin_1")
    plan = _plan(
        project, NewFindingFact(finding_id="fin_1", kind="observed", finding_set_id="fins_1"), expect={"fin_1": "live"}
    )
    assert deliveries.deliver(plan, admission=EpochAdmission.AT_OR_ABOVE) is DeliveryOutcome.RECORDED
    findings.record_fact("fin_1", kind="wont-fix", at=_LATER, note="n", actor="u1")
    revisit = DeliveryPlan(**{**plan.__dict__, "node_id": "nd_2", "epoch": 2})

    outcome = deliveries.deliver(revisit, admission=EpochAdmission.AT_OR_ABOVE)

    assert outcome is DeliveryOutcome.ALREADY_RECORDED
    assert _kinds(engine, "fin_1") == ["add", "observed", "wont-fix"]


@pytest.mark.component
def test_supersede_refuses_an_absorber_exited_after_it_was_read(tmp_path: Path) -> None:
    findings, _, engine = _world(tmp_path)
    project = default_project(engine)
    _add_finding(findings, project, "fin_a")
    _add_finding(findings, project, "fin_b")
    absorber = findings.get("fin_b")
    assert absorber is not None
    findings.record_fact("fin_b", kind="wont-fix", at=_LATER, note="n", actor="u2")
    service = FindingExitService(repo=findings, clock=FixedClock(instant=_LATER))

    with pytest.raises(AbsorberNotLive):
        service.supersede([findings.get("fin_a")], absorber, note="dup", actor="u1")  # type: ignore[list-item]

    assert _kinds(engine, "fin_a") == ["add"]


@pytest.mark.component
def test_crossing_supersedes_land_exactly_one(tmp_path: Path) -> None:
    """A→B and B→A, each having read the other as live: whichever runs second finds its absorber
    moved and is refused, so no cycle forms."""
    findings, _, engine = _world(tmp_path)
    project = default_project(engine)
    _add_finding(findings, project, "fin_a")
    _add_finding(findings, project, "fin_b")
    a, b = findings.get("fin_a"), findings.get("fin_b")
    assert a is not None and b is not None
    service = FindingExitService(repo=findings, clock=FixedClock(instant=_LATER))

    service.supersede([a], b, note="dup", actor="u1")
    with pytest.raises(AbsorberNotLive):
        service.supersede([b], a, note="dup", actor="u2")

    assert _kinds(engine, "fin_a") == ["add", "superseded"]
    assert _kinds(engine, "fin_b") == ["add"]


# --- recorder retry (unit tier) -------------------------------------------------------


class _Artifact:
    def __init__(self, name: str) -> None:
        self.data = "{}"
        self.artifact_id = f"art_{name}"


class _Artifacts:
    def latest_artifacts(self, chunk_id: str, names: Sequence[str]) -> dict[str, _Artifact]:
        return {name: _Artifact(name) for name in names}


class _Buckets:
    def __init__(self) -> None:
        self.reads = 0
        self.events: list[str] | None = None

    def for_run(self, run: RunContext) -> FindingBucket:
        self.reads += 1
        if self.events is not None:
            self.events.append("validate")
        return FindingBucket(citable=[])


class _Formats:
    def finding_delta(self, name: str, raw: str) -> Any:
        from blizzard.hub.domain.garden.formats import DeliveredDelta

        return DeliveredDelta(scope="blizzard", revisions={"blizzard": "a" * 40}, findings=[], measurement=None)

    def proposal_candidates(self, name: str, raw: str) -> list[Any]:
        return []


class _Materialize:
    def __init__(self, outcomes: list[DeliveryOutcome]) -> None:
        self.outcomes = outcomes
        self.calls = 0
        self.delivered: list[tuple[ValidatedDelivery, dict[str, object]]] = []

    def already_delivered(self, *, chunk: Chunk, node: Node, epoch: int) -> bool:
        return False

    def deliver(self, validated: ValidatedDelivery, **kwargs: object) -> DeliveryOutcome:
        self.calls += 1
        self.delivered.append((validated, kwargs))
        return self.outcomes.pop(0)


def _recorder(
    outcomes: list[DeliveryOutcome],
    resolved: list[tuple[str, str]],
    events: list[str] | None = None,
    *,
    projects: list[str] | None = None,
    with_resolver: bool = True,
) -> tuple[GardenDeliveryRecorder, Any, Any]:
    from blizzard.hub.domain.garden.delivery.validation import CommitResolution

    def resolve(repo: str, sha: str) -> CommitResolution:
        resolved.append((repo, sha))
        if events is not None:
            events.append("resolve")
        return CommitResolution(exists=True)

    def resolver_for(project_id: str) -> Any:
        if projects is not None:
            projects.append(project_id)
        return resolve

    buckets, materialize = _Buckets(), _Materialize(outcomes)
    buckets.events = events
    recorder = GardenDeliveryRecorder(
        artifacts=cast(Any, _Artifacts()),
        buckets=cast(Any, buckets),
        materialize=cast(GardenDelivery, materialize),
        formats=cast(Any, _Formats()),
        resolve_commit_for=resolver_for if with_resolver else None,
    )
    return recorder, buckets, materialize


def _record(recorder: GardenDeliveryRecorder) -> DeliveryOutcome:
    return recorder.record(
        chunk=cast(Any, type("C", (), {"chunk_id": "ch_1"})()),
        node=cast(Any, type("N", (), {"node_id": "nd_1"})()),
        epoch=1,
        run=_RUN,
        delta_names=["d"],
        proposal_names=[],
    )


@pytest.mark.unit
def test_recorder_retries_a_lost_guard_against_a_fresh_bucket_and_resolves_each_commit_once() -> None:
    resolved: list[tuple[str, str]] = []
    recorder, buckets, materialize = _recorder([DeliveryOutcome.GUARD_LOST, DeliveryOutcome.RECORDED], resolved)

    assert _record(recorder) is DeliveryOutcome.RECORDED
    assert buckets.reads == 2
    assert materialize.calls == 2
    assert resolved == [("blizzard", "a" * 40)]


@pytest.mark.unit
def test_recorder_resolves_every_commit_before_validation_starts() -> None:
    events: list[str] = []
    recorder, _, _ = _recorder([DeliveryOutcome.GUARD_LOST, DeliveryOutcome.RECORDED], [], events)

    _record(recorder)

    assert events == ["resolve", "validate", "validate"]


@pytest.mark.unit
def test_recorder_refuses_once_the_guard_attempts_run_out() -> None:
    recorder, buckets, materialize = _recorder([DeliveryOutcome.GUARD_LOST] * GUARD_ATTEMPTS, [])

    with pytest.raises(GardenDeliveryRejected) as exc:
        _record(recorder)

    assert materialize.calls == GUARD_ATTEMPTS
    assert buckets.reads == GUARD_ATTEMPTS
    assert str(exc.value) == (
        "findings this delivery names kept changing while it was being recorded — nothing was written; "
        "re-run the delivery"
    )


@pytest.mark.unit
def test_recorder_asks_the_resolver_factory_for_the_runs_project() -> None:
    projects: list[str] = []
    recorder, _, _ = _recorder([DeliveryOutcome.RECORDED], [], projects=projects)

    _record(recorder)

    assert projects == [_PROJECT]


@pytest.mark.unit
def test_recorder_with_no_resolver_factory_resolves_nothing_and_still_delivers() -> None:
    resolved: list[tuple[str, str]] = []
    recorder, _, materialize = _recorder([DeliveryOutcome.RECORDED], resolved, with_resolver=False)

    assert _record(recorder) is DeliveryOutcome.RECORDED
    assert resolved == []
    assert materialize.calls == 1


@pytest.mark.unit
def test_recorder_hands_the_node_step_and_the_delta_artifact_ids_to_the_write() -> None:
    recorder, _, materialize = _recorder([DeliveryOutcome.RECORDED], [])

    _record(recorder)

    [(validated, kwargs)] = materialize.delivered
    assert validated.run == _RUN
    assert (kwargs["epoch"], kwargs["delta_artifact_ids"], kwargs["proposal_artifact_ids"]) == (1, ["art_d"], [])
    assert (cast(Any, kwargs["chunk"]).chunk_id, cast(Any, kwargs["node"]).node_id) == ("ch_1", "nd_1")
