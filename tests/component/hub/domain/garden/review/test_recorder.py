"""The review-findings recorder a ``record-findings`` pass records through (component tier). Seeds a chunk via
``seed_work_item`` (its own resting chunk), records its ``review-finding-delta`` artifact through the artifacts
repository, and delivers it through the recorder over the hub's store — the
``tests/component/hub/domain/garden/delivery/test_recorder.py`` shape, minus the run context this recorder does not
need."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import select

from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.api.garden_formats import GardenFormats
from blizzard.hub.domain.chunk.model import WorkItemAuthor
from blizzard.hub.domain.garden.review.materialize import ReviewFindingsMaterialize, ReviewFindingsOutcome
from blizzard.hub.domain.garden.review.service import ReviewFindingsRecorder
from blizzard.hub.domain.garden.review.validation import ReviewFindingsRejected
from blizzard.hub.domain.graph.model import Graph, Node
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.review_findings_store import ReviewFindingsStore
from tests.support.hub_node_artifacts import record_hub_asset
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    chunk_stores,
    hub_scoped,
    hub_store_connections,
    seed_lease,
    seed_work_item,
)

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
    item = seed_work_item(
        store_connections, graph_id="gr_record_findings", author=WorkItemAuthor.user("u_1"), at=hub.clock.now()
    )
    return f"ch_{item.ref}"


def _record_artifact(hub: HubHarness, chunk_id: str, *, content: str, epoch: int = _EPOCH) -> None:
    assert record_hub_asset(
        hub,
        chunk_id,
        node_id=_NODE_ID,
        node_name="record-findings",
        epoch=epoch,
        name=_ARTIFACT_NAME,
        content=content,
    )


def _deferred(ref: str = "F1", *, scope: str = "blizzard", severity: str = "should-fix") -> dict[str, object]:
    return {
        "ref": ref,
        "disposition": "deferred",
        "severity": severity,
        "scope": scope,
        "class": "correctness",
        "locus": "a.py:1",
        "summary": "s",
    }


def _fixed(ref: str = "F1") -> dict[str, object]:
    return {"ref": ref, "disposition": "fixed"}


def _refuted(ref: str = "F1") -> dict[str, object]:
    return {"ref": ref, "disposition": "refuted"}


def _delta(entries: list[dict[str, object]]) -> str:
    return json.dumps({"entries": entries})


@dataclass(frozen=True)
class _Delivered:
    outcome: Literal["recorded", "invalid", "fenced"]
    detail: str = ""


def _deliver(hub: HubHarness, chunk_id: str, *, epoch: int = _EPOCH) -> _Delivered:
    """Deliver the chunk's newest review-finding delta from its record-findings node at ``epoch``."""
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None, chunk_id
    graph = hub.services.graphs.get(chunk.graph_id)
    node = graph.node_by_id(_NODE_ID) if graph is not None else None
    assert node is not None
    recorder = ReviewFindingsRecorder(
        artifacts=chunk_stores(hub.engine, hub.clock).artifacts,
        materialize=ReviewFindingsMaterialize(
            delivery=ReviewFindingsStore(hub_store_connections(hub.engine)), clock=hub.clock
        ),
        formats=GardenFormats(),
    )
    try:
        outcome = recorder.record(chunk=chunk, node=node, epoch=epoch)
    except ReviewFindingsRejected as exc:
        return _Delivered("invalid", str(exc))
    return _Delivered("fenced") if outcome is ReviewFindingsOutcome.FENCED else _Delivered("recorded")


def _finding_count(hub: HubHarness) -> int:
    with hub.engine.begin() as conn:
        return len(conn.execute(hub_scoped(select(s.findings))).all())


# --- recorded --------------------------------------------------------------


def test_a_deferred_entry_is_recorded_as_a_review_sourced_finding(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content=_delta([_deferred()]))

    delivered = _deliver(hub, chunk_id)

    assert delivered == _Delivered("recorded")
    with hub.engine.begin() as conn:
        row = conn.execute(hub_scoped(select(s.findings))).one()
    assert row.source == "review"
    assert row.severity == "should-fix"
    assert row.raised_by_chunk_id == chunk_id
    assert row.routine_name is None


def test_fixed_and_refuted_entries_mint_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content=_delta([_fixed(ref="F1"), _refuted(ref="F2")]))

    delivered = _deliver(hub, chunk_id)

    assert delivered.outcome == "recorded"
    assert _finding_count(hub) == 0


def test_a_mixed_delta_mints_only_its_deferred_entries(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content=_delta([_deferred(ref="F1"), _fixed(ref="F2"), _refuted(ref="F3")]))

    delivered = _deliver(hub, chunk_id)

    assert delivered.outcome == "recorded"
    assert _finding_count(hub) == 1


def test_an_unnamed_scope_is_minted_on_delivery(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content=_delta([_deferred(scope="brand-new")]))

    delivered = _deliver(hub, chunk_id)

    assert delivered.outcome == "recorded"
    with hub.engine.begin() as conn:
        assert conn.execute(hub_scoped(select(s.scopes).where(s.scopes.c.slug == "brand-new"))).one()


# --- invalid -----------------------------------------------------------------


def test_a_malformed_artifact_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content="not valid json")

    delivered = _deliver(hub, chunk_id)

    assert delivered.outcome == "invalid"
    assert _ARTIFACT_NAME in delivered.detail
    assert _finding_count(hub) == 0


def test_a_deferred_entry_marked_blocking_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content=_delta([_deferred(severity="blocking")]))

    delivered = _deliver(hub, chunk_id)

    assert delivered.outcome == "invalid"
    assert "blocking" in delivered.detail
    assert _finding_count(hub) == 0


def test_a_duplicate_ref_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content=_delta([_fixed(ref="F1"), _deferred(ref="F1")]))

    delivered = _deliver(hub, chunk_id)

    assert delivered.outcome == "invalid"
    assert "F1" in delivered.detail
    assert _finding_count(hub) == 0


def test_no_artifact_at_all_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)

    delivered = _deliver(hub, chunk_id)

    assert delivered.outcome == "invalid"
    assert _ARTIFACT_NAME in delivered.detail


# --- replay --------------------------------------------------------------


def test_a_replayed_delivery_still_reports_recorded_and_mints_nothing_new(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content=_delta([_deferred()]))

    first = _deliver(hub, chunk_id)
    assert first == _Delivered("recorded")
    assert _finding_count(hub) == 1

    second = _deliver(hub, chunk_id)

    assert second.outcome == "recorded"
    assert _finding_count(hub) == 1


def test_a_replay_at_a_fresh_epoch_stays_a_no_op(tmp_path: Path) -> None:
    """Idempotence is keyed on the chunk alone, so even a fresh node/epoch visit for
    an already-delivered chunk mints nothing new."""
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content=_delta([_deferred()]))
    first = _deliver(hub, chunk_id)
    assert first == _Delivered("recorded")

    second = _deliver(hub, chunk_id, epoch=_EPOCH + 1)

    assert second.outcome == "recorded"
    assert _finding_count(hub) == 1


# --- fenced ------------------------------------------------------------------


def test_a_delivery_for_a_stopped_chunk_is_fenced_and_lands_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content=_delta([_deferred()]))
    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"}).status_code == 202

    delivered = _deliver(hub, chunk_id)

    assert delivered == _Delivered("fenced")
    assert _finding_count(hub) == 0
    with hub.engine.begin() as conn:
        assert (
            conn.execute(
                hub_scoped(select(s.artifacts).where(s.artifacts.c.name == "review-findings-delivered"))
            ).first()
            is None
        )


def test_a_delivery_a_restart_superseded_is_fenced_and_lands_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, content=_delta([_deferred()]))
    seed_lease(hub.engine, chunk_id, epoch=2, runner_id="r1", at=hub.clock.now())

    delivered = _deliver(hub, chunk_id, epoch=1)

    assert delivered == _Delivered("fenced")
    assert _finding_count(hub) == 0
