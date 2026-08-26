"""The strike fact and its atomicity: ``DecisionService.resolve``'s struck-id set,
validated against the chunk's pending proposals and written inside
``record_decision_resolution``'s own transaction, against a real, migrated store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from blizzard.hub.store import schema as s
from tests.support import HubHarness, build_hub

pytestmark = pytest.mark.component

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


def _create_proposal(*, title: str) -> dict:
    return {"kind": "create", "title": title, "body": "do it", "stated_priority": "normal"}


def _ingest(hub: HubHarness, *, ref: str = "9") -> tuple[str, str]:
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _GATE_YAML})
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


def _submit_decision(hub: HubHarness, chunk_id: str, node_id: str, *, proposals: list[dict], epoch: int = 1) -> str:
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/decisions",
        json={"from_node_id": node_id, "epoch": epoch, "runner_id": "r1", "artifacts": [], "proposals": proposals},
    )
    assert resp.status_code == 200, resp.text
    decision = hub.services.chunks.decision_for_chunk(chunk_id)
    assert decision is not None
    return decision.decision_id


def _deliver_past_gate(hub: HubHarness, chunk_id: str, node_id: str, decision_id: str, *, epoch: int = 1) -> None:
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": epoch,
            "runner_id": "r1",
            "from_node_id": node_id,
            "decision_id": decision_id,
            "artifacts": [],
            "proposals": [],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "done", resp.text


def _proposal_ids_by_title(hub: HubHarness, chunk_id: str) -> dict[str, str]:
    with hub.engine.connect() as conn:
        rows = conn.execute(select(s.work_item_proposals).where(s.work_item_proposals.c.chunk_id == chunk_id)).all()
    return {json.loads(r.data)["title"]: r.proposal_id for r in rows}


def _strike_rows(hub: HubHarness) -> dict[str, tuple[str, str]]:
    """``proposal_id`` -> ``(decision_id, struck_by)`` for every recorded strike."""
    with hub.engine.connect() as conn:
        rows = conn.execute(select(s.work_item_strikes)).all()
    return {r.proposal_id: (r.decision_id, r.struck_by) for r in rows}


def test_resolving_with_a_subset_strikes_exactly_those_and_leaves_the_rest_pending(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _ingest(hub)
    decision_id = _submit_decision(
        hub, chunk_id, node_id, proposals=[_create_proposal(title="keep"), _create_proposal(title="strike")]
    )
    ids = _proposal_ids_by_title(hub, chunk_id)

    result = hub.services.decisions.resolve(decision_id, choice="pass", resolved_by="alice", struck=[ids["strike"]])

    assert result is not None and result.resolved
    strikes = _strike_rows(hub)
    assert set(strikes) == {ids["strike"]}
    assert strikes[ids["strike"]] == (decision_id, "alice")

    decision = hub.services.chunks.get_decision(decision_id)
    assert decision is not None
    pending = {e.proposal.proposal_id for e in decision.docket if not e.struck}
    assert pending == {ids["keep"]}


def test_resolving_with_an_empty_set_strikes_nothing_and_resolves_normally(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _ingest(hub)
    decision_id = _submit_decision(hub, chunk_id, node_id, proposals=[_create_proposal(title="only")])

    result = hub.services.decisions.resolve(decision_id, choice="pass", resolved_by="alice")

    assert result is not None and result.resolved
    assert _strike_rows(hub) == {}


def test_a_proposal_id_not_pending_for_the_chunk_is_rejected_and_writes_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _ingest(hub)
    decision_id = _submit_decision(hub, chunk_id, node_id, proposals=[_create_proposal(title="only")])

    with pytest.raises(ValueError):
        hub.services.decisions.resolve(decision_id, choice="pass", resolved_by="alice", struck=["wip_bogus"])

    assert _strike_rows(hub) == {}
    decision = hub.services.chunks.get_decision(decision_id)
    assert decision is not None and not decision.resolved


def test_the_cas_loser_writes_no_strike_when_its_ids_are_disjoint_from_the_winners(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _ingest(hub)
    decision_id = _submit_decision(
        hub, chunk_id, node_id, proposals=[_create_proposal(title="one"), _create_proposal(title="two")]
    )
    ids = _proposal_ids_by_title(hub, chunk_id)

    won = hub.services.decisions.resolve(decision_id, choice="pass", resolved_by="alice", struck=[ids["one"]])
    lost = hub.services.decisions.resolve(decision_id, choice="fail", resolved_by="bob", struck=[ids["two"]])

    assert won is not None and won.resolved
    assert lost is not None and not lost.resolved
    assert set(_strike_rows(hub)) == {ids["one"]}


def test_the_cas_loser_writes_no_strike_even_when_its_ids_overlap_the_winners(tmp_path: Path) -> None:
    """The realistic race: both operators saw the same open docket and each toggled
    "one". The loser's resolve call must not 400 on an id the winner just struck out
    from under it — it should fall through to the CAS and be told who won, exactly as
    a resolve naming no overlapping ids would."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _ingest(hub)
    decision_id = _submit_decision(
        hub, chunk_id, node_id, proposals=[_create_proposal(title="one"), _create_proposal(title="two")]
    )
    ids = _proposal_ids_by_title(hub, chunk_id)

    won = hub.services.decisions.resolve(decision_id, choice="pass", resolved_by="alice", struck=[ids["one"]])
    lost = hub.services.decisions.resolve(
        decision_id, choice="fail", resolved_by="bob", struck=[ids["one"], ids["two"]]
    )

    assert won is not None and won.resolved
    assert lost is not None and not lost.resolved
    assert set(_strike_rows(hub)) == {ids["one"]}


def test_after_delivery_the_sweep_materializes_every_unstruck_proposal_and_no_struck_one(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _ingest(hub)
    decision_id = _submit_decision(
        hub, chunk_id, node_id, proposals=[_create_proposal(title="keep"), _create_proposal(title="strike")]
    )
    ids = _proposal_ids_by_title(hub, chunk_id)
    hub.services.decisions.resolve(decision_id, choice="pass", resolved_by="alice", struck=[ids["strike"]])
    _deliver_past_gate(hub, chunk_id, node_id, decision_id)

    hub.services.work_item_materialization.sweep()

    items = hub.client.get("/api/work-sources/hub/items").json()["items"]
    assert {i["title"] for i in items} == {"keep"}
