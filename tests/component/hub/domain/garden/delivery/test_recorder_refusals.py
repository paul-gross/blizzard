"""Garden-delivery refusals at the recorder (component tier): any named delta that does not
resolve refuses the whole delivery, a named proposals artifact that does not resolve is
tolerated, and a routine-run proposal with a blank title, class, or body is refused."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from blizzard.hub.store import schema as s
from tests.support.garden_delivery_world import (
    SCOPE,
    Delivered,
    add_op,
    deliver,
    finding_count,
    garden_delta,
    record_artifact,
    seed_chunk,
    seed_scope,
)
from tests.support.scaffolding import build_hub, hub_scoped


def test_a_missing_delta_beside_a_resolving_one_refuses_the_delivery(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[add_op()]))

    delivered = deliver(hub, chunk_id, delta=["delta", "ghost-delta"])

    assert delivered.outcome == "invalid"
    assert "ghost-delta" in delivered.detail
    assert finding_count(hub) == 0


def test_a_missing_proposals_artifact_is_tolerated(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[add_op()]))

    delivered = deliver(hub, chunk_id, delta=["delta"], proposals=["docket"])

    assert delivered == Delivered("recorded")
    assert finding_count(hub) == 1


@pytest.mark.parametrize("field", ["title", "class", "body"])
def test_a_proposal_with_a_blank_field_refuses_the_delivery(tmp_path: Path, field: str) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[add_op()]))
    candidate: dict[str, object] = {"ref": "p1", "class": "c", "title": "t", "body": "b", "findings": []}
    candidate[field] = "   "
    record_artifact(hub, chunk_id, name="docket", content=json.dumps([candidate]))

    delivered = deliver(hub, chunk_id, delta=["delta"], proposals=["docket"])

    assert delivered == Delivered("invalid", f"proposal 'p1' has a blank {field}")
    assert finding_count(hub) == 0
    with hub.engine.begin() as conn:
        assert conn.execute(hub_scoped(select(s.garden_proposals))).all() == []
