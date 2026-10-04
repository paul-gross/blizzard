"""A retired scope stays nameable (component tier): its description can still be edited
and it can still be linked into a routine's declared set, while a run against it is
still refused — retiring withdraws a scope from selection only."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub

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


def test_editing_a_retired_scopes_description_is_legal_and_it_stays_retired(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    hub.client.post("/api/scopes", json={"slug": "cold", "description": "old"})
    hub.client.post("/api/scopes/cold/retire", json={"by": "operator"})

    resp = hub.client.patch("/api/scopes/cold", json={"description": "new"})

    assert resp.status_code == 200, resp.text
    assert (resp.json()["description"], resp.json()["retired"]) == ("new", True)


def test_linking_a_retired_scope_is_legal_and_a_run_against_it_is_still_refused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH}).status_code == 201
    routine = hub.client.post(
        "/api/routines", json={"name": "nightly", "graph_name": "alpha", "default_scope_slug": "blizzard"}
    ).json()
    hub.client.post("/api/scopes", json={"slug": "cold", "description": ""})
    hub.client.post("/api/scopes/cold/retire", json={"by": "operator"})

    linked = hub.client.put(f"/api/routines/{routine['routine_id']}/scopes/cold")
    run = hub.client.post(f"/api/routines/{routine['routine_id']}/run", json={"scope_slug": "cold", "mode": "full"})

    assert linked.status_code == 204, linked.text
    assert hub.client.get(f"/api/routines/{routine['routine_id']}/scopes").json() == ["blizzard", "cold"]
    assert run.status_code == 503, run.text
    assert run.json()["detail"] == "scope 'cold' is retired"
