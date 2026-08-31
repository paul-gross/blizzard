"""Garden delivery route — the hub-executed delivery node's own POST (blizzard#393
Phase 4, component tier). Seeds a chunk via ``seed_work_item`` (its own resting chunk),
records a run context through ``RunContextStore`` (Phase 1), and posts delta/proposal
artifacts recorded via ``services.chunks.record_hub_artifact`` — the
``test_hub_marker_auth`` shape, minus the OAuth gate (``build_hub``'s default
``auth_mode=none`` grants everything without a session)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert, select

from blizzard.foundation.ids import FINDING_PREFIX, Id
from blizzard.hub.domain.run_context import RunContext
from blizzard.hub.domain.work import WorkItemAuthor
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.run_context_store import RunContextStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import HubHarness, build_hub, hub_store_connections, seed_graph, seed_work_item

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
_NODE_ID = "nd_deliver"
_EPOCH = 1
_ROUTINE = "nightly"
_SCOPE = "blizzard"


def _seed_scope(hub: HubHarness, slug: str) -> None:
    with hub.engine.begin() as conn:
        conn.execute(insert(s.scopes).values(slug=slug, description="", created_at=_NOW))


def _seed_chunk(hub: HubHarness, *, with_run_context: bool = True) -> str:
    """A work item with its own resting chunk (issue #357's own two-step mint), plus a
    recorded run context for it (Phase 1) unless ``with_run_context`` is False — the
    chunk id the delivery route resolves through."""
    store_connections = hub_store_connections(hub.engine)
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_delivery", at=_NOW)
    items = WorkItemStore(store_connections)
    item = seed_work_item(items, graph_id="gr_delivery", author=WorkItemAuthor.user("u_1"), at=_NOW)
    if with_run_context:
        RunContextStore(store_connections).record(
            item.work_item_id, RunContext(routine_name=_ROUTINE, scope_slug=_SCOPE, mode="dry_run")
        )
    return f"ch_{item.ref}"


def _seed_finding(hub: HubHarness, finding_id: str, *, scope_slug: str = _SCOPE, routine_name: str = _ROUTINE) -> None:
    FindingStore(hub_store_connections(hub.engine)).add(
        finding_id,
        routine_name=routine_name,
        scope_slug=scope_slug,
        class_="stale-docstring",
        locus="a.py:1",
        summary="s",
        introduced=None,
        at=_NOW,
    )


def _record_artifact(hub: HubHarness, chunk_id: str, *, name: str, content: str, epoch: int = _EPOCH) -> None:
    assert hub.services.hub_node.record_marker(
        chunk_id, node_id=_NODE_ID, node_name="deliver", epoch=epoch, name=name, content=content
    )


def _delta(*, scope: str = _SCOPE, revisions: dict[str, str] | None = None, findings: list[dict] | None = None) -> str:
    return json.dumps({"scope": scope, "revisions": revisions or {}, "measurement": None, "findings": findings or []})


def _add_op(*, class_: str = "c", locus: str = "a.py:1", summary: str = "s") -> dict:
    return {"op": "add", "class": class_, "locus": locus, "summary": summary}


def _observed_op(finding_id: str) -> dict:
    return {"op": "observed", "id": finding_id}


def _gone_op(finding_id: str, *, note: str = "n") -> dict:
    return {"op": "gone", "id": finding_id, "note": note}


def _proposals(*, findings: list[str]) -> str:
    return json.dumps([{"ref": "p1", "class": "c", "title": "t", "body": "b", "findings": findings}])


def _post(hub: HubHarness, chunk_id: str, *, delta=(), proposals=(), node_id: str = _NODE_ID, epoch: int = _EPOCH):
    return hub.client.post(
        f"/api/chunks/{chunk_id}/garden-delivery?node_id={node_id}&epoch={epoch}",
        json={"delta": list(delta), "proposals": list(proposals)},
    )


def _finding_count(hub: HubHarness) -> int:
    with hub.engine.begin() as conn:
        return conn.execute(select(s.findings)).all().__len__()


# --- recorded --------------------------------------------------------------


def test_a_clean_addition_is_recorded(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, _SCOPE)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, name="delta", content=_delta(findings=[_add_op()]))

    resp = _post(hub, chunk_id, delta=["delta"])

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"outcome": "recorded", "detail": ""}
    assert _finding_count(hub) == 1


def test_additions_only_mints_every_finding(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, _SCOPE)
    chunk_id = _seed_chunk(hub)
    _record_artifact(
        hub, chunk_id, name="delta", content=_delta(findings=[_add_op(locus="a.py:1"), _add_op(locus="b.py:2")])
    )

    resp = _post(hub, chunk_id, delta=["delta"])

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "recorded"
    assert _finding_count(hub) == 2


def test_transformations_only_mints_no_new_finding(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, _SCOPE)
    chunk_id = _seed_chunk(hub)
    finding_id = Id.mint(FINDING_PREFIX, hub.clock).value
    _seed_finding(hub, finding_id)
    _record_artifact(hub, chunk_id, name="delta", content=_delta(findings=[_observed_op(finding_id)]))

    resp = _post(hub, chunk_id, delta=["delta"])

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "recorded"
    assert _finding_count(hub) == 1  # the pre-seeded finding alone — nothing new minted


def test_a_mixed_delta_with_proposals_is_recorded(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, _SCOPE)
    chunk_id = _seed_chunk(hub)
    finding_id = Id.mint(FINDING_PREFIX, hub.clock).value
    _seed_finding(hub, finding_id)
    _record_artifact(hub, chunk_id, name="delta", content=_delta(findings=[_add_op(), _gone_op(finding_id)]))
    _record_artifact(hub, chunk_id, name="docket", content=_proposals(findings=[finding_id]))

    resp = _post(hub, chunk_id, delta=["delta"], proposals=["docket"])

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "recorded"
    assert _finding_count(hub) == 2  # the pre-seeded one plus the freshly-added one
    with hub.engine.begin() as conn:
        assert conn.execute(select(s.garden_proposals)).all()


# --- invalid -----------------------------------------------------------------


def test_a_malformed_artifact_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, _SCOPE)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, name="delta", content="not valid json")

    resp = _post(hub, chunk_id, delta=["delta"])

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "invalid"
    assert "delta" in body["detail"]
    assert _finding_count(hub) == 0


def test_an_unknown_finding_id_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, _SCOPE)
    chunk_id = _seed_chunk(hub)
    ghost = Id.mint(FINDING_PREFIX, hub.clock).value
    _record_artifact(hub, chunk_id, name="delta", content=_delta(findings=[_observed_op(ghost)]))

    resp = _post(hub, chunk_id, delta=["delta"])

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "invalid"
    assert ghost in body["detail"]
    assert _finding_count(hub) == 0


def test_an_out_of_scope_transformation_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, _SCOPE)
    _seed_scope(hub, "other-scope")
    chunk_id = _seed_chunk(hub)
    finding_id = Id.mint(FINDING_PREFIX, hub.clock).value
    _seed_finding(hub, finding_id, scope_slug="other-scope")
    _record_artifact(hub, chunk_id, name="delta", content=_delta(scope=_SCOPE, findings=[_observed_op(finding_id)]))

    resp = _post(hub, chunk_id, delta=["delta"])

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "invalid"
    assert "scope" in body["detail"]


def test_an_unresolved_delta_artifact_is_invalid_naming_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, _SCOPE)
    chunk_id = _seed_chunk(hub)

    resp = _post(hub, chunk_id, delta=["ghost-artifact"])

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "invalid"
    assert "ghost-artifact" in body["detail"]


# --- replay --------------------------------------------------------------


def test_a_replayed_delivery_still_reports_recorded_and_mints_nothing_new(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, _SCOPE)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, name="delta", content=_delta(findings=[_add_op()]))

    first = _post(hub, chunk_id, delta=["delta"])
    assert first.status_code == 200 and first.json()["outcome"] == "recorded"
    assert _finding_count(hub) == 1

    second = _post(hub, chunk_id, delta=["delta"])

    assert second.status_code == 200, second.text
    assert second.json()["outcome"] == "recorded"
    assert _finding_count(hub) == 1  # nothing new minted on replay


# --- edges -----------------------------------------------------------------


def test_an_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = _post(hub, "ch_ghost", delta=["delta"])

    assert resp.status_code == 404, resp.text


def test_a_chunk_with_no_run_context_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, _SCOPE)
    chunk_id = _seed_chunk(hub, with_run_context=False)

    resp = _post(hub, chunk_id, delta=["delta"])

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "invalid"
    assert chunk_id in body["detail"]
