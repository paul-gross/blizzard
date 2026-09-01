"""blizzard#366 Phase 2 — ``WorkItemMaterializationReconciler.sweep()`` against a real,
migrated store: a delivered chunk's proposals become real work items (``create``) or
appended evidence (``update``), an unresolvable proposal is recorded with its reason,
and a transient failure leaves the proposal for the next pass. Inverts
``tests/test_work_item_proposals_apply.py::test_proposals_ride_the_completion_inertly_then_materialize_once_swept``."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.domain.work import WorkItemAuthorKind, WorkItemClosure, WorkItemMaterializationOutcome, WorkRef
from blizzard.hub.graphs import PACKAGED
from blizzard.hub.store import schema as s
from tests.support import HubHarness, build_hub

pytestmark = pytest.mark.component

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
          description: Terminal.
          to: done
        fail:
          description: Retry.
          to: build
"""


def _create_proposal(*, title: str = "New idea", body: str = "do it", stated_priority: str = "high") -> dict:
    return {"kind": "create", "title": title, "body": body, "stated_priority": stated_priority}


def _update_proposal(*, source: str, ref: str, evidence: str = "fixed") -> dict:
    return {"kind": "update", "source": source, "ref": ref, "evidence": evidence}


def _ingest(hub: HubHarness, yaml_body: str = _DELIVER_TO_DONE_YAML, *, ref: str = "9") -> tuple[str, str]:
    graph = hub.client.post("/api/graphs", json={"definition_yaml": yaml_body})
    assert graph.status_code == 201, graph.text
    node_id = next(n["node_id"] for n in graph.json()["nodes"] if n["name"] == "build")
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [f"default:{ref}"]}).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    resp = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": 1, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": 1}}],
        },
    )
    assert resp.status_code == 200, resp.text
    return chunk_id, node_id


def _deliver(hub: HubHarness, chunk_id: str, node_id: str, *, proposals: list[dict], epoch: int = 1) -> dict:
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": epoch,
            "runner_id": "r1",
            "from_node_id": node_id,
            "artifacts": [],
            "proposals": proposals,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "done", resp.text
    return resp.json()


def _hub_items(hub: HubHarness) -> list[dict]:
    return hub.client.get("/api/work-sources/hub/items").json()["items"]


def _materialization_rows(hub: HubHarness) -> dict:
    """Every ``work_item_materializations`` row recorded so far, keyed by ``(source,
    ref)`` to ``(outcome, reason)``."""
    with hub.engine.connect() as conn:
        rows = conn.execute(select(s.work_item_materializations)).all()
    return {(r.source, r.ref): (r.outcome, r.reason) for r in rows}


# --- `create` materialization ---------------------------------------------------


def test_create_proposal_materializes_into_a_hub_item_with_fleet_authorship_and_a_resting_chunk(
    tmp_path: Path,
) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _ingest(hub)
    _deliver(hub, chunk_id, node_id, proposals=[_create_proposal(title="ship the thing")])

    hub.services.work_item_materialization.sweep()

    items = _hub_items(hub)
    assert len(items) == 1
    item = items[0]
    assert item["title"] == "ship the thing"
    assert item["author"]["kind"] == WorkItemAuthorKind.FLEET.value
    assert item["author"]["runner_id"] == "r1"
    assert item["author"]["chunk_id"] == chunk_id
    assert item["author"]["node_name"] == "build"

    holder = hub.services.chunks.work_refs.find_live_holder(WorkRef(source="hub", ref=item["ref"]))
    assert holder is not None and holder != chunk_id  # its own fresh chunk, not the proposing one
    facts = hub.services.chunks.facts.load_facts(holder)
    assert facts is not None
    assert facts.status().value == "not_ready"


def test_a_second_sweep_mints_no_duplicate_item_and_records_no_second_outcome(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _ingest(hub)
    _deliver(hub, chunk_id, node_id, proposals=[_create_proposal()])

    hub.services.work_item_materialization.sweep()
    hub.services.work_item_materialization.sweep()

    assert len(_hub_items(hub)) == 1
    assert len(_materialization_rows(hub)) == 1


def test_proposals_from_two_epochs_of_the_same_node_both_materialize(tmp_path: Path) -> None:
    """D3: no epoch filter — every fence-accepted proposal row materializes, including
    one from an earlier epoch that retried before the chunk went on to deliver."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _ingest(hub)
    # A first attempt fails and retries — its own proposal still lands with the transition.
    retry = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "fail",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": node_id,
            "artifacts": [],
            "proposals": [_create_proposal(title="fail once")],
        },
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["outcome"] != "failure", retry.text
    resp = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": 2, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": 2}}],
        },
    )
    assert resp.status_code == 200, resp.text
    _deliver(hub, chunk_id, node_id, proposals=[_create_proposal(title="then pass")], epoch=2)

    hub.services.work_item_materialization.sweep()

    titles = {item["title"] for item in _hub_items(hub)}
    assert titles == {"fail once", "then pass"}


# --- `update` materialization ----------------------------------------------------


def test_update_proposal_appends_evidence_and_stamps_edited_at(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _ingest(hub, ref="10")
    created = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "original"})
    assert created.status_code == 201, created.text
    ref = created.json()["ref"]

    _deliver(hub, chunk_id, node_id, proposals=[_update_proposal(source="hub", ref=ref, evidence="landed the fix")])

    hub.services.work_item_materialization.sweep()

    item = hub.client.get(f"/api/work-sources/hub/items/{ref}").json()
    assert item["body"] == "original\n\nlanded the fix"
    assert item["edited_at"] == iso_utc(hub.clock.now())  # stamped by the composite write, not left as created_at


def test_unresolvable_update_cases_are_recorded_with_reason_and_siblings_still_materialize(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _ingest(hub, ref="11")
    open_item = hub.client.post("/api/work-sources/hub/items", json={"title": "open", "body": "b"}).json()
    withdrawn_item = hub.client.post("/api/work-sources/hub/items", json={"title": "withdrawn", "body": "b"}).json()
    assert hub.client.delete(f"/api/work-sources/hub/items/{withdrawn_item['ref']}").status_code == 200

    _deliver(
        hub,
        chunk_id,
        node_id,
        proposals=[
            _update_proposal(source="hub", ref=withdrawn_item["ref"], evidence="too late"),
            _update_proposal(source="hub", ref="no-such-ref", evidence="nowhere"),
            _update_proposal(source="default", ref="1", evidence="no editor on this source"),
            _update_proposal(source="hub", ref=open_item["ref"], evidence="lands fine"),
        ],
    )

    hub.services.work_item_materialization.sweep()

    reread = hub.client.get(f"/api/work-sources/hub/items/{open_item['ref']}").json()
    assert reread["body"] == "b\n\nlands fine"
    withdrawn_reread = hub.client.get(f"/api/work-sources/hub/items/{withdrawn_item['ref']}").json()
    assert withdrawn_reread["body"] == "b"  # untouched
    assert withdrawn_reread["closure"] == WorkItemClosure.WITHDRAWN.value

    rows = _materialization_rows(hub)
    assert len(rows) == 4
    outcome, reason = rows[("hub", withdrawn_item["ref"])]
    assert outcome == WorkItemMaterializationOutcome.UNRESOLVED.value
    assert reason == f"item is {WorkItemClosure.WITHDRAWN.value}"
    outcome, reason = rows[("hub", "no-such-ref")]
    assert outcome == WorkItemMaterializationOutcome.UNRESOLVED.value
    assert reason == "item does not exist"
    outcome, reason = rows[("default", "1")]
    assert outcome == WorkItemMaterializationOutcome.UNRESOLVED.value
    assert reason == "source 'default' has no editor"
    outcome, reason = rows[("hub", open_item["ref"])]
    assert outcome == WorkItemMaterializationOutcome.UPDATED.value
    assert reason is None


# --- transient failures leave the proposal unjudged -----------------------------


def test_a_retired_default_graph_leaves_the_create_proposal_unjudged_until_re_enabled(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    # The delivering chunk's own graph shares the packaged default's name; retiring it
    # after the chunk is already claimed on it leaves that claim untouched (issue #101)
    # while starving `ensure_default()`'s later name resolution for the reconciler's mint.
    chunk_id, node_id = _ingest(hub, ref="12")
    graphs = hub.client.get("/api/graphs").json()
    default_graph = next(g for g in graphs if g["name"] == PACKAGED.default.doc.name)
    retired = hub.client.post(f"/api/graphs/{default_graph['graph_id']}/retire", json={"by": "operator"})
    assert retired.status_code == 202, retired.text

    _deliver(hub, chunk_id, node_id, proposals=[_create_proposal(title="blocked on a retired graph")])

    hub.services.work_item_materialization.sweep()  # must not raise

    titles = {item["title"] for item in _hub_items(hub)}
    assert "blocked on a retired graph" not in titles

    enabled = hub.client.post(f"/api/graphs/{default_graph['graph_id']}/enable", json={"by": "operator"})
    assert enabled.status_code == 202, enabled.text

    hub.services.work_item_materialization.sweep()

    titles = {item["title"] for item in _hub_items(hub)}
    assert "blocked on a retired graph" in titles


def test_a_pre_empted_ref_leaves_the_create_proposal_unjudged(tmp_path: Path) -> None:
    """``IngestConflict``: an out-of-band ingest of ``hub:1`` — the ref the reconciler's
    own ``allocate_ref`` would mint next — pre-empts it. The burned ref is never retried,
    so the very next sweep succeeds under a fresh one instead of colliding forever."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _ingest(hub, ref="13")
    preempted = hub.client.post("/api/chunks", json={"tokens": ["hub:1"]})
    assert preempted.status_code == 201, preempted.text

    _deliver(hub, chunk_id, node_id, proposals=[_create_proposal(title="loses the race")])

    hub.services.work_item_materialization.sweep()  # must not raise

    assert _hub_items(hub) == []
    assert _materialization_rows(hub) == {}  # not recorded terminal — left for the next pass

    hub.services.work_item_materialization.sweep()

    titles = {item["title"] for item in _hub_items(hub)}
    assert "loses the race" in titles
