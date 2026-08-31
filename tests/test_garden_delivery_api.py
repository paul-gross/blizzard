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
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.graph import Graph, Node
from blizzard.hub.domain.run_context import RunContext
from blizzard.hub.domain.work import WorkItemAuthor
from blizzard.hub.graphs.scripts import garden_deliver, land_common
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.run_context_store import RunContextStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import HubHarness, build_hub, hub_store_connections, seed_work_item

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
_NODE_ID = "nd_deliver"
_EPOCH = 1
_ROUTINE = "nightly"
_SCOPE = "blizzard"


def _seed_scope(hub: HubHarness, slug: str) -> None:
    with hub.engine.begin() as conn:
        conn.execute(insert(s.scopes).values(slug=slug, description="", created_at=_NOW))


def _deliver_node(node_id: str = _NODE_ID, *, graph_id: str = "gr_delivery") -> Node:
    return Node(
        node_id=node_id,
        graph_id=graph_id,
        name="deliver",
        executor=Executor.HUB,
        prompt=None,
        checks=[],
        produces=[],
        session=SessionMode.FRESH,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
        mode=None,
    )


def _seed_chunk(hub: HubHarness, *, with_run_context: bool = True) -> str:
    """A work item with its own resting chunk (issue #357's own two-step mint), plus a
    recorded run context for it (Phase 1) unless ``with_run_context`` is False — the
    chunk id the delivery route resolves through. The graph carries the one node the
    route's ``node_id`` names, so ``graph.node_by_id`` resolves it."""
    store_connections = hub_store_connections(hub.engine)
    node = _deliver_node()
    graph = Graph(graph_id="gr_delivery", name="g", entry_node_id=node.node_id, nodes=[node], edges=[], created_at=_NOW)
    GraphStore(store_connections).mint(graph, definition_yaml="", at=_NOW)
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


def test_an_unknown_node_id_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, _SCOPE)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, name="delta", content=_delta(findings=[_add_op()]))

    resp = _post(hub, chunk_id, delta=["delta"], node_id="nd_ghost")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "invalid"
    assert "nd_ghost" in body["detail"]
    assert _finding_count(hub) == 0


def test_a_delta_declaring_a_scope_other_than_the_runs_own_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, _SCOPE)
    _seed_scope(hub, "other-scope")
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, name="delta", content=_delta(scope="other-scope", findings=[_add_op()]))

    resp = _post(hub, chunk_id, delta=["delta"])

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "invalid"
    assert "other-scope" in body["detail"]
    assert _SCOPE in body["detail"]
    assert _finding_count(hub) == 0


def test_a_repeated_delta_artifact_name_still_records_cleanly(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, _SCOPE)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, name="delta", content=_delta(findings=[_add_op()]))

    resp = _post(hub, chunk_id, delta=["delta", "delta"])

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"outcome": "recorded", "detail": ""}
    assert _finding_count(hub) == 1


def test_an_observed_op_revives_a_previously_gone_finding(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, _SCOPE)
    chunk_id = _seed_chunk(hub)
    finding_id = Id.mint(FINDING_PREFIX, hub.clock).value
    _seed_finding(hub, finding_id)
    FindingStore(hub_store_connections(hub.engine)).record_fact(finding_id, kind="gone", at=_NOW, note="fixed")
    _record_artifact(hub, chunk_id, name="delta", content=_delta(findings=[_observed_op(finding_id)]))

    resp = _post(hub, chunk_id, delta=["delta"])

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"outcome": "recorded", "detail": ""}


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


# --- the script over the route ------------------------------------------------


def _run_script(
    hub: HubHarness,
    chunk_id: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    delta: tuple[str, ...] = (),
    proposals: tuple[str, ...] = (),
) -> int:
    """`garden_deliver.main()` driving this harness's own app — the script's one HTTP
    seam (`land_common.forge_request`) forwarded to the TestClient, so its real argv,
    injected env, request body and token header meet the real route over a real store."""

    def _through_the_hub(method: str, url: str, *, token=None, body=None, headers=None):  # type: ignore[no-untyped-def]
        resp = hub.client.request(method, url, json=body, headers=headers)
        return resp.status_code, (resp.json() if resp.content else None)

    monkeypatch.setattr(land_common, "forge_request", _through_the_hub)
    visit = f"node_id={_NODE_ID}&epoch={_EPOCH}"
    monkeypatch.setenv("BZ_HUB_CHUNK_ID", chunk_id)
    monkeypatch.setenv("BZ_HUB_NODE_ID", _NODE_ID)
    monkeypatch.setenv("BZ_HUB_EPOCH", str(_EPOCH))
    monkeypatch.setenv("BZ_HUB_MARKER_TOKEN", "tok")
    monkeypatch.setenv("BZ_HUB_GARDEN_DELIVERY_URL", f"http://testserver/api/chunks/{chunk_id}/garden-delivery?{visit}")
    monkeypatch.setenv("BZ_HUB_MARKER_CALLBACK_URL", f"http://testserver/api/chunks/{chunk_id}/hub-markers?{visit}")
    argv = ["garden_deliver"]
    for name in delta:
        argv += ["--delta", name]
    for name in proposals:
        argv += ["--proposals", name]
    monkeypatch.setattr("sys.argv", argv)
    return garden_deliver.main()


def _artifact_content(hub: HubHarness, chunk_id: str, name: str) -> str | None:
    with hub.engine.begin() as conn:
        row = conn.execute(
            select(s.artifacts).where((s.artifacts.c.chunk_id == chunk_id) & (s.artifacts.c.name == name))
        ).first()
    return None if row is None else row.data


def test_the_script_delivers_through_the_route_and_prints_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, _SCOPE)
    chunk_id = _seed_chunk(hub)
    finding_id = Id.mint(FINDING_PREFIX, hub.clock).value
    _seed_finding(hub, finding_id)
    _record_artifact(hub, chunk_id, name="delta", content=_delta(findings=[_add_op(), _observed_op(finding_id)]))
    _record_artifact(hub, chunk_id, name="docket", content=_proposals(findings=[finding_id]))

    exit_code = _run_script(hub, chunk_id, monkeypatch, delta=("delta",), proposals=("docket",))

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "recorded"
    assert _finding_count(hub) == 2  # the pre-seeded one plus the freshly-added one
    with hub.engine.begin() as conn:
        assert conn.execute(select(s.garden_proposals)).all()


def test_the_scripts_failure_marker_is_durable_on_an_invalid_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, _SCOPE)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, name="delta", content="not valid json")

    exit_code = _run_script(hub, chunk_id, monkeypatch, delta=("delta",))

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "invalid"
    assert _finding_count(hub) == 0
    failure = _artifact_content(hub, chunk_id, "garden-delivery-failure")
    assert failure is not None and "delta" in failure


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
