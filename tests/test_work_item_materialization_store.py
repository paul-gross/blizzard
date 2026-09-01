"""blizzard#366 Phase 1 — the schema and the repository seams materialization needs:
``work_item_proposals.runner_id`` stamped at every proposal-carrying write lane, the D2
candidate read (``unmaterialized_proposals``), and D8's two atomic composite writes
(``materialize_create``/``materialize_update``) plus the standalone ``unresolved``
recorder. The reconciler that drives these (Phase 2) lives in
``tests/test_work_item_materialization.py``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.proposals import WorkItemProposalRow
from blizzard.hub.domain.work import (
    IWriteWorkItemRepository,
    WorkItemAuthor,
    WorkItemClosure,
    WorkItemMaterializationOutcome,
    WorkRef,
    mint_chunk,
)
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import (
    HubHarness,
    build_hub,
    chunk_stores,
    hub_store_connections,
    migrate_to,
    seed_chunk,
    seed_graph,
    seed_work_item,
)

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_DELIVER_TO_DONE_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build.
    proposes_work_items: true
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Runner-node terminal.
          to: done
        fail:
          description: Retry.
          to: build
"""

_GATE_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build.
    proposes_work_items: true
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Ready.
          to: done
        fail:
          description: Retry.
          to: build
"""

_TRIAGE_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Triage.
    proposes_work_items: true
    judgement:
      prompt: Assess.
      choices:
        migrate:
          description: Hand off.
          to: graph:target
        fail:
          description: Retry.
          to: build
"""

_TARGET_YAML = """
name: target
entry: build
nodes:
  build:
    executor: runner
    prompt: Land.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Done.
          to: done
        fail:
          description: Retry.
          to: build
"""


def _create_proposal(*, title: str = "New idea") -> dict:
    return {"kind": "create", "title": title, "body": "do it", "stated_priority": "high"}


def _ingest(hub: HubHarness, yaml_body: str, *, claim_runner: str = "r-route") -> tuple[str, dict]:
    graph = hub.client.post("/api/graphs", json={"definition_yaml": yaml_body})
    assert graph.status_code == 201, graph.text
    nodes = {n["name"]: n["node_id"] for n in graph.json()["nodes"]}
    chunk_id = hub.client.post("/api/chunks", json={"tokens": ["default:9"]}).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": claim_runner, "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    resp = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": claim_runner,
            "facts": [{"seq": 1, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": 1}}],
        },
    )
    assert resp.status_code == 200, resp.text
    return chunk_id, nodes


def _complete(hub: HubHarness, chunk_id: str, node_id: str, *, choice: str, runner_id: str, epoch: int = 1) -> dict:
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": choice,
            "epoch": epoch,
            "runner_id": runner_id,
            "from_node_id": node_id,
            "artifacts": [],
            "proposals": [_create_proposal()],
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _submit_decision(hub: HubHarness, chunk_id: str, node_id: str, *, runner_id: str, epoch: int = 1) -> dict:
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/decisions",
        json={
            "from_node_id": node_id,
            "epoch": epoch,
            "runner_id": runner_id,
            "artifacts": [],
            "proposals": [_create_proposal()],
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _stored_runner_ids(hub: HubHarness, chunk_id: str) -> list[str | None]:
    with hub.engine.connect() as conn:
        rows = conn.execute(
            select(s.work_item_proposals.c.runner_id).where(s.work_item_proposals.c.chunk_id == chunk_id)
        ).all()
    return [r.runner_id for r in rows]


def _proposal_row(chunk_id: str, proposal_id: str, *, node_id: str = "nd_1") -> WorkItemProposalRow:
    return WorkItemProposalRow(
        proposal_id=proposal_id,
        chunk_id=chunk_id,
        node_id=node_id,
        node_name="build",
        epoch=1,
        ordinal=0,
        kind="create",
        data="{}",
        runner_id="r1",
    )


# --- Migration: existing chunks, proposals, and items stay readable -----------


def test_upgrade_adds_runner_id_and_materializations_leaving_existing_rows_readable(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, "20260825_1100_work_item_proposals")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        seed_chunk(conn, "ch_legacy", graph_id="gr_1", at=_T0)
        conn.execute(
            sa.insert(s.work_item_proposals).values(
                proposal_id="wip_legacy",
                chunk_id="ch_legacy",
                node_id="nd_1",
                node_name="build",
                epoch=1,
                ordinal=0,
                kind="create",
                data="{}",
                proposed_at=_T0,
            )
        )
    work_item_store = WorkItemStore(hub_store_connections(engine))
    author = WorkItemAuthor.user("u1")
    legacy_item = seed_work_item(work_item_store, graph_id="gr_1", author=author, at=_T0)

    runner.upgrade("head")

    assert "work_item_materializations" in sa.inspect(engine).get_table_names()
    with engine.connect() as conn:
        proposal_row = conn.execute(
            select(s.work_item_proposals).where(s.work_item_proposals.c.proposal_id == "wip_legacy")
        ).one()
    assert proposal_row.runner_id is None  # nullable, no backfill
    assert work_item_store.get(legacy_item.source, legacy_item.ref) == legacy_item


# --- runner_id stamped at every proposal-carrying write lane -------------------


def test_runner_id_is_stamped_on_a_transition_and_is_the_submissions_not_the_routes(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, nodes = _ingest(hub, _DELIVER_TO_DONE_YAML, claim_runner="r-route")

    _complete(hub, chunk_id, nodes["build"], choice="pass", runner_id="r-submit")

    assert _stored_runner_ids(hub, chunk_id) == ["r-submit"]


def test_runner_id_is_stamped_on_a_decision_open(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, nodes = _ingest(hub, _GATE_YAML, claim_runner="r-route")

    _submit_decision(hub, chunk_id, nodes["build"], runner_id="r-submit")

    assert _stored_runner_ids(hub, chunk_id) == ["r-submit"]


def test_runner_id_is_stamped_on_a_cross_graph_migration(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _TARGET_YAML}).status_code == 201
    chunk_id, nodes = _ingest(hub, _TRIAGE_YAML, claim_runner="r-route")

    _complete(hub, chunk_id, nodes["build"], choice="migrate", runner_id="r-submit")

    assert _stored_runner_ids(hub, chunk_id) == ["r-submit"]


# --- IReadChunkDeliveryRepository.unmaterialized_proposals() — the D2 candidate read --


def test_candidate_read_covers_both_delivery_paths_and_excludes_non_delivered(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunks = chunk_stores(hub.engine, hub.clock)

    def _mint() -> str:
        chunk = mint_chunk([], graph_id="gr_x", at=_T0)
        chunks.record.mint(chunk)
        return chunk.chunk_id

    runner_terminal = _mint()
    hub_terminal = _mint()
    stopped_after_delivery = _mint()
    never_delivered = _mint()
    hand_completed = _mint()
    grouped_after_delivery = _mint()

    # A runner node routing straight to `done` via the ordinary `record_transition`.
    chunks.movement.record_transition(
        transition_id="tr_runner",
        chunk_id=runner_terminal,
        from_node_id="nd_1",
        to_node_id="done",
        choice_name="pass",
        epoch=1,
        runner_id="r1",
        at=_T0,
        artifacts=[],
        proposals=[_proposal_row(runner_terminal, "wip_runner")],
    )

    # A hub node routing to `done` via `record_hub_step_transition`.
    chunks.movement.record_transition(
        transition_id="tr_hub_pre",
        chunk_id=hub_terminal,
        from_node_id="nd_1",
        to_node_id="nd_2",
        choice_name="pass",
        epoch=1,
        runner_id="r1",
        at=_T0,
        artifacts=[],
        proposals=[_proposal_row(hub_terminal, "wip_hub")],
    )
    chunks.hub_exec.record_hub_step_transition(
        hub_terminal,
        from_node_id="nd_2",
        to_node_id="done",
        choice_name="success",
        epoch=1,
        runner_id="hub",
        transition_id="tr_hub_done",
        at=_T0,
        artifacts=[],
        release_route=True,
    )

    # Delivered, then later stopped — still counts as delivered (D2's did-it-deliver reading).
    chunks.movement.record_transition(
        transition_id="tr_stopped_after",
        chunk_id=stopped_after_delivery,
        from_node_id="nd_1",
        to_node_id="done",
        choice_name="pass",
        epoch=1,
        runner_id="r1",
        at=_T0,
        artifacts=[],
        proposals=[_proposal_row(stopped_after_delivery, "wip_stopped_after")],
    )
    chunks.lifecycle.record_stop(stopped_after_delivery, by="operator", at=_T0)

    # Never delivered — parked mid-graph, no terminal transition.
    chunks.movement.record_transition(
        transition_id="tr_never",
        chunk_id=never_delivered,
        from_node_id="nd_1",
        to_node_id="nd_2",
        choice_name="pass",
        epoch=1,
        runner_id="r1",
        at=_T0,
        artifacts=[],
        proposals=[_proposal_row(never_delivered, "wip_never")],
    )

    # Hand-completed by an operator — `chunk_completed`, no transition at all.
    chunks.movement.record_transition(
        transition_id="tr_hand",
        chunk_id=hand_completed,
        from_node_id="nd_1",
        to_node_id="nd_2",
        choice_name="pass",
        epoch=1,
        runner_id="r1",
        at=_T0,
        artifacts=[],
        proposals=[_proposal_row(hand_completed, "wip_hand")],
    )
    chunks.lifecycle.record_completion(hand_completed, by="operator", at=_T0)

    # Delivered, then grouped away — ephemeral now, excluded like a deleted chunk.
    chunks.movement.record_transition(
        transition_id="tr_grouped",
        chunk_id=grouped_after_delivery,
        from_node_id="nd_1",
        to_node_id="done",
        choice_name="pass",
        epoch=1,
        runner_id="r1",
        at=_T0,
        artifacts=[],
        proposals=[_proposal_row(grouped_after_delivery, "wip_grouped")],
    )
    chunks.lifecycle.record_grouped(grouped_after_delivery, grouped_into=runner_terminal, at=_T0)

    candidates = {row.proposal_id for row in chunks.delivery.unmaterialized_proposals()}

    assert candidates == {"wip_runner", "wip_hub", "wip_stopped_after"}


def test_candidate_read_excludes_an_already_judged_proposal(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunks = chunk_stores(hub.engine, hub.clock)
    chunk = mint_chunk([], graph_id="gr_x", at=_T0)
    chunks.record.mint(chunk)
    chunks.movement.record_transition(
        transition_id="tr_1",
        chunk_id=chunk.chunk_id,
        from_node_id="nd_1",
        to_node_id="done",
        choice_name="pass",
        epoch=1,
        runner_id="r1",
        at=_T0,
        artifacts=[],
        proposals=[_proposal_row(chunk.chunk_id, "wip_judged")],
    )

    assert len(chunks.delivery.unmaterialized_proposals()) == 1

    chunks.delivery.record_work_item_materialization(
        "wip_judged", outcome=WorkItemMaterializationOutcome.UNRESOLVED, pointer=None, reason="no proposer", at=_T0
    )

    assert chunks.delivery.unmaterialized_proposals() == []


# --- D8's two composite writes: all-or-nothing, idempotent per proposal_id ----


def test_record_work_item_materialization_returns_false_on_a_second_call(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunks = chunk_stores(hub.engine, hub.clock)

    first = chunks.delivery.record_work_item_materialization(
        "wip_1", outcome=WorkItemMaterializationOutcome.UNRESOLVED, pointer=None, reason="no proposer", at=_T0
    )
    second = chunks.delivery.record_work_item_materialization(
        "wip_1", outcome=WorkItemMaterializationOutcome.UNRESOLVED, pointer=None, reason="no proposer", at=_T0
    )

    assert first is True
    assert second is False
    with hub.engine.connect() as conn:
        rows = conn.execute(
            select(s.work_item_materializations).where(s.work_item_materializations.c.proposal_id == "wip_1")
        ).all()
    assert len(rows) == 1


def test_materialize_create_mints_the_item_chunk_and_outcome_atomically_and_is_idempotent(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    engine = hub.engine
    graph_id = hub.client.post("/api/graphs", json={"definition_yaml": _GATE_YAML}).json()["graph_id"]
    store = hub_store_connections(engine)
    items = cast(IWriteWorkItemRepository, WorkItemStore(store))
    author = WorkItemAuthor.fleet(runner_id="r1", chunk_id="ch_source", node_name="build")

    graph = hub.services.graphs.get(graph_id)
    assert graph is not None
    pointer = WorkRef(source="hub", ref=items.allocate_ref("hub"))
    chunk = mint_chunk([pointer], graph_id=graph.graph_id, at=_T0)

    first = items.materialize_create(
        proposal_id="wip_create_1",
        pointer=pointer,
        title="t",
        body="b",
        author=author,
        stated_priority=None,
        at=_T0,
        chunk=chunk,
    )
    assert first is True
    assert items.get("hub", pointer.ref) is not None
    assert chunk_stores(engine, FixedClock(_T0)).record.get(chunk.chunk_id) is not None

    second = items.materialize_create(
        proposal_id="wip_create_1",
        pointer=pointer,
        title="t",
        body="b",
        author=author,
        stated_priority=None,
        at=_T0,
        chunk=chunk,
    )
    assert second is False  # already judged — nothing minted a second time

    with engine.connect() as conn:
        outcomes = conn.execute(
            select(s.work_item_materializations).where(s.work_item_materializations.c.proposal_id == "wip_create_1")
        ).all()
    assert len(outcomes) == 1
    assert outcomes[0].outcome == WorkItemMaterializationOutcome.CREATED.value


def test_materialize_update_appends_evidence_stamps_edited_at_and_is_idempotent(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    engine = hub.engine
    items = cast(IWriteWorkItemRepository, WorkItemStore(hub_store_connections(engine)))
    created = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "original"})
    assert created.status_code == 201, created.text
    ref = created.json()["ref"]

    first = items.materialize_update(proposal_id="wip_update_1", source="hub", ref=ref, evidence="fixed it", at=_T0)
    assert first is True
    reread = items.get("hub", ref)
    assert reread is not None
    assert reread.body == "original\n\nfixed it"
    assert reread.edited_at == _T0

    second = items.materialize_update(proposal_id="wip_update_1", source="hub", ref=ref, evidence="again", at=_T0)
    assert second is False  # already judged — no double append

    reread = items.get("hub", ref)
    assert reread is not None
    assert reread.body == "original\n\nfixed it"


def test_materialize_update_writes_nothing_when_the_item_is_no_longer_open(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    engine = hub.engine
    items = cast(IWriteWorkItemRepository, WorkItemStore(hub_store_connections(engine)))
    created = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"})
    ref = created.json()["ref"]
    items.close("hub", ref, closure=WorkItemClosure.WITHDRAWN, at=_T0)

    result = items.materialize_update(proposal_id="wip_closed", source="hub", ref=ref, evidence="too late", at=_T0)

    assert result is False
    with engine.connect() as conn:
        outcomes = conn.execute(
            select(s.work_item_materializations).where(s.work_item_materializations.c.proposal_id == "wip_closed")
        ).all()
    assert outcomes == []
