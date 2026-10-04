"""Garden-delivery refusals at the route (component tier): any named delta that does not
resolve refuses the whole delivery, a named proposals artifact that does not resolve is
tolerated, and a routine-run proposal with a blank title, class, or body is refused."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from blizzard.hub.store import schema as s
from tests.support import build_hub
from tests.test_garden_delivery_api import (
    _SCOPE,
    _add_op,
    _delta,
    _finding_count,
    _post,
    _record_artifact,
    _seed_chunk,
    _seed_scope,
)

pytestmark = pytest.mark.component


def test_a_missing_delta_beside_a_resolving_one_refuses_the_delivery(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, _SCOPE)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, name="delta", content=_delta(findings=[_add_op()]))

    resp = _post(hub, chunk_id, delta=["delta", "ghost-delta"])

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "invalid"
    assert "ghost-delta" in body["detail"]
    assert _finding_count(hub) == 0


def test_a_missing_proposals_artifact_is_tolerated(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, _SCOPE)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, name="delta", content=_delta(findings=[_add_op()]))

    resp = _post(hub, chunk_id, delta=["delta"], proposals=["docket"])

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"outcome": "recorded", "detail": ""}
    assert _finding_count(hub) == 1


@pytest.mark.parametrize("field", ["title", "class", "body"])
def test_a_proposal_with_a_blank_field_refuses_the_delivery(tmp_path: Path, field: str) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, _SCOPE)
    chunk_id = _seed_chunk(hub)
    _record_artifact(hub, chunk_id, name="delta", content=_delta(findings=[_add_op()]))
    candidate = {"ref": "p1", "class": "c", "title": "t", "body": "b", "findings": []}
    candidate[field] = "   "
    _record_artifact(hub, chunk_id, name="docket", content=json.dumps([candidate]))

    resp = _post(hub, chunk_id, delta=["delta"], proposals=["docket"])

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"outcome": "invalid", "detail": f"proposal 'p1' has a blank {field}"}
    assert _finding_count(hub) == 0
    with hub.engine.begin() as conn:
        assert conn.execute(select(s.garden_proposals)).all() == []
