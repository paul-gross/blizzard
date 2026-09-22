"""Review-findings-delivery route — the `record-findings` node's own POST (blizzard#582
Phase 1, component tier). Seeds a chunk via ``seed_work_item`` (its own resting chunk)
and posts a ``review-finding-delta`` artifact recorded via
``services.hub_node.record_marker`` — the ``tests/test_garden_delivery_api.py`` shape,
minus the run-context seam this route does not need."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.graph import Graph, Node
from blizzard.hub.domain.work import WorkItemAuthor
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import HubHarness, build_hub, hub_store_connections, seed_work_item

pytestmark = pytest.mark.component

_NODE_ID = "nd_record_findings"
_EPOCH = 1
_ARTIFACT_NAME = "review-finding-delta"


def _record_findings_node(node_id: str = _NODE_ID, *, graph_id: str = "gr_record_findings") -> Node:
    return Node(
        node_id=node_id,
        graph_id=graph_id,
        name="record-findings",
        executor=Executor.HUB,
        prompt=None,
        checks=[],
        produces=[],
        session=SessionMode.FRESH,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
    )


def _seed_chunk(hub: HubHarness) -> str:
    store_connections = hub_store_connections(hub.engine)
    node = _record_findings_node()
    graph = Graph(
        graph_id="gr_record_findings",
        name="g",
        entry_node_id=node.node_id,
        nodes=[node],
        edges=[],
        created_at=hub.clock.now(),
    )
    GraphStore(store_connections).mint(graph, definition_yaml="", at=hub.clock.now())
    items = WorkItemStore(store_connections)
    item = seed_work_item(items, graph_id="gr_record_findings", author=WorkItemAuthor.user("u_1"), at=hub.clock.now())
    return f"ch_{item.ref}"


def _record_artifact(hub: HubHarness, chunk_id: str, *, content: str, epoch: int = _EPOCH) -> None:
    assert hub.services.hub_node.record_marker(
        chunk_id, node_id=_NODE_ID, node_name="record-findings", epoch=epoch, name=_ARTIFACT_NAME, content=content
    )


def _deferred(ref: str = "F1", *, scope: str = "blizzard", severity: str = "should-fix") -> dict:
    return {
        "ref": ref,
        "disposition": "deferred",
        "severity": severity,
        "scope": scope,
        "class": "correctness",
        "locus": "a.py:1",
        "summary": "s",
    }


def _fixed(ref: str = "F1") -> dict:
    return {"ref": ref, "disposition": "fixed"}


def _refuted(ref: str = "F1") -> dict:
    return {"ref": ref, "disposition": "refuted"}


def _delta(entries: list[dict]) -> str:
    return json.dumps({"entries": entries})


def _post(hub: HubHarness, chunk_id: str, *, node_id: str = _NODE_ID, epoch: int = _EPOCH):
    return hub.client.post(
        f"/api/chunks/{chunk_id}/review-findings-delivery?node_id={node_id}&epoch={epoch}", json=None
    )


def _finding_count(hub: HubHarness) -> int:
    with hub.engine.begin() as conn:
        return len(conn.execute(select(s.findings)).all())


# --- recorded --------------------------------------------------------------


def test_a_deferred_entry_is_recorded_as_a_review_sourced_finding(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content=_delta([_deferred()]))

    resp = _post(hub, chunk_id)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"outcome": "recorded", "detail": ""}
    with hub.engine.begin() as conn:
        row = conn.execute(select(s.findings)).one()
    assert row.source == "review"
    assert row.severity == "should-fix"
    assert row.raised_by_chunk_id == chunk_id
    assert row.routine_name is None


def test_fixed_and_refuted_entries_mint_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content=_delta([_fixed(ref="F1"), _refuted(ref="F2")]))

    resp = _post(hub, chunk_id)

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "recorded"
    assert _finding_count(hub) == 0


def test_a_mixed_delta_mints_only_its_deferred_entries(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content=_delta([_deferred(ref="F1"), _fixed(ref="F2"), _refuted(ref="F3")]))

    resp = _post(hub, chunk_id)

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "recorded"
    assert _finding_count(hub) == 1


def test_an_unnamed_scope_is_minted_on_delivery(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content=_delta([_deferred(scope="brand-new")]))

    resp = _post(hub, chunk_id)

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "recorded"
    with hub.engine.begin() as conn:
        assert conn.execute(select(s.scopes).where(s.scopes.c.slug == "brand-new")).one()


# --- invalid -----------------------------------------------------------------


def test_a_malformed_artifact_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content="not valid json")

    resp = _post(hub, chunk_id)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "invalid"
    assert _ARTIFACT_NAME in body["detail"]
    assert _finding_count(hub) == 0


def test_a_deferred_entry_marked_blocking_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content=_delta([_deferred(severity="blocking")]))

    resp = _post(hub, chunk_id)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "invalid"
    assert "blocking" in body["detail"]
    assert _finding_count(hub) == 0


def test_a_duplicate_ref_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content=_delta([_fixed(ref="F1"), _deferred(ref="F1")]))

    resp = _post(hub, chunk_id)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "invalid"
    assert "F1" in body["detail"]
    assert _finding_count(hub) == 0


def test_no_artifact_at_all_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)

    resp = _post(hub, chunk_id)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "invalid"
    assert _ARTIFACT_NAME in body["detail"]


def test_an_unknown_node_id_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content=_delta([_deferred()]))

    resp = _post(hub, chunk_id, node_id="nd_ghost")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "invalid"
    assert "nd_ghost" in body["detail"]
    assert _finding_count(hub) == 0


def test_an_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = _post(hub, "ch_ghost")

    assert resp.status_code == 404, resp.text


# --- replay --------------------------------------------------------------


def test_a_replayed_delivery_still_reports_recorded_and_mints_nothing_new(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content=_delta([_deferred()]))

    first = _post(hub, chunk_id)
    assert first.status_code == 200 and first.json()["outcome"] == "recorded"
    assert _finding_count(hub) == 1

    second = _post(hub, chunk_id)

    assert second.status_code == 200, second.text
    assert second.json()["outcome"] == "recorded"
    assert _finding_count(hub) == 1


def test_a_replay_at_a_fresh_epoch_stays_a_no_op(tmp_path: Path) -> None:
    """D6: idempotence is keyed on the chunk alone, so even a fresh node/epoch visit for
    an already-delivered chunk mints nothing new."""
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content=_delta([_deferred()]))
    first = _post(hub, chunk_id)
    assert first.status_code == 200 and first.json()["outcome"] == "recorded"

    second = _post(hub, chunk_id, epoch=_EPOCH + 1)

    assert second.status_code == 200, second.text
    assert second.json()["outcome"] == "recorded"
    assert _finding_count(hub) == 1
