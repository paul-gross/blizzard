"""Routine routes — create, list, read, and edit (blizzard#389, component tier).

Proves the HTTP surface end to end: a create/edit naming a graph with no enabled mint
422s naming it, a duplicate name is refused on create, a name change is refused on
edit naming the current one, and naming an unseen default scope mints it through the
same path ``POST /scopes`` uses — the ``tests/test_graph_lifecycle_api.py`` shape."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub

pytestmark = pytest.mark.component

_GRAPH_A = """
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

_GRAPH_B = """
name: beta
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


def _mint_graph(hub, definition_yaml: str = _GRAPH_A) -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/graphs", json={"definition_yaml": definition_yaml})
    assert resp.status_code == 201, resp.text


def _create(hub, **overrides: object) -> dict:  # type: ignore[no-untyped-def, type-arg]
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


def test_create_mints_a_routine_carrying_an_rtn_prefixed_id(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)

    body = _create(hub)

    assert body["routine_id"].startswith("rtn_")
    assert body["name"] == "nightly"
    assert body["graph_name"] == "alpha"
    assert body["default_scope_slug"] == "blizzard"


def test_create_mints_the_named_default_scope(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)

    _create(hub, default_scope_slug="fresh-scope")

    resp = hub.client.get("/api/scopes/fresh-scope")
    assert resp.status_code == 200, resp.text


def test_create_naming_an_existing_name_is_refused_rather_than_duplicating(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    _create(hub)

    resp = hub.client.post(
        "/api/routines",
        json={"name": "nightly", "graph_name": "alpha", "default_scope_slug": "blizzard"},
    )

    assert resp.status_code == 422, resp.text
    assert "nightly" in resp.json()["detail"]
    assert len(hub.client.get("/api/routines").json()) == 1


def test_create_naming_an_unresolved_graph_is_refused_naming_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.post(
        "/api/routines",
        json={"name": "nightly", "graph_name": "ghost", "default_scope_slug": "blizzard"},
    )

    assert resp.status_code == 422, resp.text
    assert "ghost" in resp.json()["detail"]


def test_create_rejects_a_malformed_default_scope_slug_naming_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)

    resp = hub.client.post(
        "/api/routines",
        json={"name": "nightly", "graph_name": "alpha", "default_scope_slug": "Not A Slug"},
    )

    assert resp.status_code == 422, resp.text
    assert "Not A Slug" in resp.json()["detail"]


def test_edit_naming_a_different_name_is_refused_naming_the_current_one(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create(hub)

    resp = hub.client.patch(
        f"/api/routines/{routine['routine_id']}",
        json={"name": "renamed", "graph_name": "alpha", "default_scope_slug": "blizzard"},
    )

    assert resp.status_code == 422, resp.text
    assert "nightly" in resp.json()["detail"]


def test_edit_changes_graph_default_scope_and_model_effort_defaults(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub, _GRAPH_A)
    _mint_graph(hub, _GRAPH_B)
    routine = _create(hub)

    resp = hub.client.patch(
        f"/api/routines/{routine['routine_id']}",
        json={
            "name": "nightly",
            "graph_name": "beta",
            "default_scope_slug": "other-scope",
            "default_model": ["blizzard:advanced"],
            "default_effort": "high",
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["graph_name"] == "beta"
    assert body["default_scope_slug"] == "other-scope"
    assert body["default_model"] == ["blizzard:advanced"]
    assert body["default_effort"] == "high"


def test_edit_naming_an_unresolved_graph_is_refused_naming_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create(hub)

    resp = hub.client.patch(
        f"/api/routines/{routine['routine_id']}",
        json={"name": "nightly", "graph_name": "ghost", "default_scope_slug": "blizzard"},
    )

    assert resp.status_code == 422, resp.text
    assert "ghost" in resp.json()["detail"]


def test_edit_unknown_routine_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.patch(
        "/api/routines/rtn_ghost",
        json={"name": "x", "graph_name": "alpha", "default_scope_slug": "blizzard"},
    )
    assert resp.status_code == 404


def test_get_unknown_routine_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.get("/api/routines/rtn_ghost")
    assert resp.status_code == 404


def test_show_returns_the_whole_record(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    created = _create(hub, default_model=["basic"], default_effort="low")

    resp = hub.client.get(f"/api/routines/{created['routine_id']}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "nightly"
    assert body["graph_name"] == "alpha"
    assert body["default_scope_slug"] == "blizzard"
    assert body["default_model"] == ["basic"]
    assert body["default_effort"] == "low"


def test_list_returns_every_routine(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    _create(hub, name="a")
    _create(hub, name="b")

    names = {row["name"] for row in hub.client.get("/api/routines").json()}

    assert names == {"a", "b"}
