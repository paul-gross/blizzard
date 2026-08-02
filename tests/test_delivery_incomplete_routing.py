"""Retrospective's authored ``delivery-incomplete`` choice actually routes to ``resolve``,
carrying the ``resolve.from-retrospective.md`` addendum (component tier, issue #238 AC4).

Mints the **real, packaged** advanced-development-workflow graph — not a hand-rolled
copy — so a regression in the packaged file itself (the choice dropped, retargeted, or
its addendum broken) fails here, not just the unit-tier reify assertion
(``tests/test_graph_authoring.py``). Driving a chunk through the graph's other six nodes
(``plan -> plan-review -> build -> verify -> review -> pre-push -> deliver``) to reach
``retrospective`` for real is exactly what the packaged ``land_pr_ci`` script and the
generic hub-command-node machinery already have their own component coverage for; this
scenario is scoped to the one thing under test — the routing edge itself — so it seeds
the chunk directly at ``retrospective``'s real, minted node id via a direct
transition-fact insert, the same technique
``tests/test_transition_graph_provenance.py``'s component tier uses to reach a scenario
without re-driving every preceding node-step over HTTP.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.hub.graphs import _GRAPHS_DIR, inline_graph_yaml
from blizzard.hub.store import schema as s
from tests.support import HubHarness, build_hub, pointer_token, report_lease

pytestmark = pytest.mark.component


def _mint_and_claim(hub: HubHarness) -> tuple[str, dict[str, str]]:
    """Mint the packaged adv-dwf graph, ingest a chunk (which pins to the hub's own
    packaged **default** graph — ingest names no graph), then repin it onto adv-dwf via
    ``PATCH /chunks/{id}`` (legal while the chunk is still ``not_ready``) before
    claiming a route, so every node id resolved off the mint response is the one the
    claimed chunk's pin actually recognizes."""
    definition_yaml = inline_graph_yaml(_GRAPHS_DIR / "advanced-development-workflow" / "graph.yaml")
    minted = hub.client.post("/api/graphs", json={"definition_yaml": definition_yaml})
    assert minted.status_code == 201, minted.text
    graph_id = minted.json()["graph_id"]
    nodes = {n["name"]: n["node_id"] for n in minted.json()["nodes"]}
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "1"})]}
    ).json()["chunk_id"]
    repin = hub.client.patch(f"/api/chunks/{chunk_id}", json={"graph_id": graph_id})
    assert repin.status_code == 202, repin.text
    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e1"]},
    )
    return chunk_id, nodes


def _seed_at_retrospective(hub: HubHarness, chunk_id: str, graph_id: str, node_id: str) -> None:
    """Place the chunk's current node directly at ``retrospective`` via a synthetic
    transition fact — only ``to_node_id`` matters to :func:`current_node_id`'s
    newest-transition derivation, so this stands in for the six node-steps a real chunk
    would have taken to arrive here. Recorded at epoch 1, under retrospective's real
    attempt at epoch 2 (below): ``newest_transition`` tie-breaks same-instant
    transitions by epoch, and a ``FixedClock`` never advances on its own, so two
    same-epoch transitions here would be genuinely ambiguous — the same reason
    ``test_poll_timeout_escalates_once_the_bounce_cap_is_crossed`` gives its own
    re-entry a fresh epoch."""
    with hub.engine.begin() as conn:
        conn.execute(
            insert(s.transitions).values(
                transition_id="tr_seed_1",
                chunk_id=chunk_id,
                graph_id=graph_id,
                from_node_id=None,
                to_node_id=node_id,
                choice_name=None,
                epoch=1,
                runner_id="r1",
                recorded_at=hub.clock.now(),
            )
        )


def test_delivery_incomplete_routes_to_resolve_with_its_addendum(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, nodes = _mint_and_claim(hub)
    graph_id = hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"]
    _seed_at_retrospective(hub, chunk_id, graph_id, nodes["retrospective"])
    report_lease(hub, chunk_id, epoch=2, seq=1)

    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "delivery-incomplete",
            "epoch": 2,
            "runner_id": "r1",
            "from_node_id": nodes["retrospective"],
        },
    )
    body = resp.json()
    assert body["outcome"] == "next", body

    # The accepted transition's target node is `resolve` — the named assertion a
    # mutation proof (deleting the choice from graph.yaml) must break, not merely the
    # suite's aggregate exit status.
    assert body["next_envelope"]["node"]["node_id"] == nodes["resolve"]

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["current_node_id"] == nodes["resolve"]
    transition = detail["history"][-1]
    assert transition["to_node_id"] == nodes["resolve"]
    assert transition["choice_name"] == "delivery-incomplete"

    # The re-entry envelope carries the resolve.from-retrospective.md addendum, appended
    # to resolve.md's base prompt — content unique to the addendum, not just "resolve"
    # (which the base prompt also says).
    prompt = body["next_envelope"]["prompt"]
    assert "re-entering the **resolve** node" in prompt
    assert "partial" in prompt
