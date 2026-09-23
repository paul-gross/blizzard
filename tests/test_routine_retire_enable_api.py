"""Routine retire/enable routes (component tier). Proves the HTTP surface
end to end: the brake is reversible and idempotent, a retired routine drops out of the
default list but stays fully readable everywhere else, and a run against it refuses —
the ``tests/test_scope_lifecycle_api.py`` shape."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.hub.store.internal.finding_store import FindingStore
from tests.support import build_hub, hub_store_connections

pytestmark = pytest.mark.component

_GRAPH = """
name: alpha
entry: build
nodes:
  build:
    executor: runner
    prompt: do the work
    judgement:
      prompt: judge it
      choices:
        pass:
          description: it works
          to: done
"""

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _mint_graph(hub, definition_yaml: str = _GRAPH) -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/graphs", json={"definition_yaml": definition_yaml})
    assert resp.status_code == 201, resp.text


def _create_routine(hub, **overrides: object) -> dict:  # type: ignore[no-untyped-def, type-arg]
    body: dict[str, object] = {
        "name": "nightly",
        "graph_name": "alpha",
        "default_scope_slug": "blizzard",
        "default_model": [],
        "default_effort": None,
    }
    body.update(overrides)
    resp = hub.client.post("/api/routines", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_retire_returns_202_and_the_view_reports_retired(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)

    resp = hub.client.post(f"/api/routines/{routine['routine_id']}/retire", json={"by": "paul"})

    assert resp.status_code == 202, resp.text
    assert resp.json()["retired"] is True


def test_enable_reverses_a_retire(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)
    hub.client.post(f"/api/routines/{routine['routine_id']}/retire", json={"by": "operator"})

    resp = hub.client.post(f"/api/routines/{routine['routine_id']}/enable", json={"by": "operator"})

    assert resp.status_code == 202, resp.text
    assert resp.json()["retired"] is False
    assert hub.client.get(f"/api/routines/{routine['routine_id']}").json()["retired"] is False


def test_a_second_retire_is_a_harmless_no_op(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)
    hub.client.post(f"/api/routines/{routine['routine_id']}/retire", json={"by": "operator"})

    resp = hub.client.post(f"/api/routines/{routine['routine_id']}/retire", json={"by": "operator"})

    assert resp.status_code == 202, resp.text
    assert resp.json()["retired"] is True


def test_retire_defaults_by_to_operator(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)

    resp = hub.client.post(f"/api/routines/{routine['routine_id']}/retire", json={})

    assert resp.status_code == 202, resp.text
    assert resp.json()["retired"] is True


def test_retire_unknown_routine_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/routines/rtn_ghost/retire", json={"by": "operator"})
    assert resp.status_code == 404


def test_enable_unknown_routine_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/routines/rtn_ghost/enable", json={"by": "operator"})
    assert resp.status_code == 404


def test_get_reads_the_retired_state(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)

    hub.client.post(f"/api/routines/{routine['routine_id']}/retire", json={})

    assert hub.client.get(f"/api/routines/{routine['routine_id']}").json()["retired"] is True


def test_list_excludes_a_retired_routine_by_default(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    _create_routine(hub, name="live")
    retired = _create_routine(hub, name="retired-one")
    hub.client.post(f"/api/routines/{retired['routine_id']}/retire", json={})

    names = {row["name"] for row in hub.client.get("/api/routines").json()}

    assert names == {"live"}


def test_list_includes_and_marks_a_retired_routine_on_request(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    _create_routine(hub, name="live")
    retired = _create_routine(hub, name="retired-one")
    hub.client.post(f"/api/routines/{retired['routine_id']}/retire", json={})

    rows = {row["name"]: row for row in hub.client.get("/api/routines", params={"include_retired": "true"}).json()}

    assert set(rows) == {"live", "retired-one"}
    assert rows["retired-one"]["retired"] is True
    assert rows["live"]["retired"] is False


def test_run_against_a_retired_routine_is_503_naming_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)
    hub.client.post(f"/api/routines/{routine['routine_id']}/retire", json={})

    resp = hub.client.post(f"/api/routines/{routine['routine_id']}/run", json={})

    assert resp.status_code == 503, resp.text
    assert routine["name"] in resp.json()["detail"]


def test_run_against_a_retired_routine_is_refused_before_its_scope_is_resolved(tmp_path: Path) -> None:
    """A scope override no scope row holds is a 422 on a live routine; on a retired one
    the retire refusal comes first, and the unknown slug stays unminted."""
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)
    hub.client.post(f"/api/routines/{routine['routine_id']}/retire", json={})

    resp = hub.client.post(f"/api/routines/{routine['routine_id']}/run", json={"scope_slug": "ghost"})

    assert resp.status_code == 503, resp.text
    assert routine["name"] in resp.json()["detail"]
    assert hub.client.get("/api/scopes/ghost").status_code == 404


def test_run_after_enable_succeeds_again(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)
    hub.client.post(f"/api/routines/{routine['routine_id']}/retire", json={})
    hub.client.post(f"/api/routines/{routine['routine_id']}/enable", json={})

    resp = hub.client.post(f"/api/routines/{routine['routine_id']}/run", json={})

    assert resp.status_code == 201, resp.text


def test_retiring_deletes_nothing_findings_and_proposals_stay_readable(tmp_path: Path) -> None:
    """a routine's findings, proposals, and closures are untouched
    by retiring it."""
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)
    finding_store = FindingStore(hub_store_connections(hub.engine))
    finding = finding_store.add(
        "fnd_1",
        routine_name=routine["name"],
        scope_slug="blizzard",
        class_="style",
        locus="src/example.py:1",
        summary="an example finding",
        introduced=None,
        at=_NOW,
    )
    proposal = hub.services.garden_proposal_authoring.create(
        routine_name=routine["name"], class_="style", title="tidy it up", body="", findings=[finding]
    )
    hub.services.garden_proposal_closure.pass_(proposal, reason="not needed", by="operator")

    before_findings = hub.client.get("/api/findings").json()
    before_proposal = hub.client.get(f"/api/garden-proposals/{proposal.proposal_id}").json()

    resp = hub.client.post(f"/api/routines/{routine['routine_id']}/retire", json={})
    assert resp.status_code == 202, resp.text

    after_findings = hub.client.get("/api/findings").json()
    after_proposal = hub.client.get(f"/api/garden-proposals/{proposal.proposal_id}").json()
    assert after_findings == before_findings
    assert after_proposal == before_proposal

    sweeps = hub.client.get(
        f"/api/routines/{routine['routine_id']}/sweeps",
        params={"since": "2026-01-01T00:00:00Z", "until": "2026-12-31T00:00:00Z"},
    )
    assert sweeps.status_code == 200, sweeps.text
    trend = hub.client.get(
        "/api/routines/trend",
        params={
            "routine": routine["name"],
            "since": "2026-01-01T00:00:00Z",
            "until": "2026-12-31T00:00:00Z",
            "introduced_boundary": "2026-01-01T00:00:00Z",
        },
    )
    assert trend.status_code == 200, trend.text
