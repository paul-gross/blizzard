"""Graph retire/re-enable — ``POST /api/graphs/{id}/retire`` and ``.../enable`` (#101).

Proves the HTTP surface end to end: the ``graphs`` row never changes, the wire's
``enabled``/``retired`` fields flip, ``GET /api/graphs``'s ``effective`` marker falls
back to the newest non-retired version (or ``None`` once all are retired), and 404s.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.hub.domain.graph_authoring import DefaultGraphRetired
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
        fail:
          description: it does not
          to: build
"""


def _mint(hub, definition_yaml: str = _GRAPH_A) -> str:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/graphs", json={"definition_yaml": definition_yaml})
    assert resp.status_code == 201, resp.text
    return resp.json()["graph_id"]


def test_retire_returns_202_and_the_view_reports_retired(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    graph_id = _mint(hub)

    resp = hub.client.post(f"/api/graphs/{graph_id}/retire", json={"by": "paul"})

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["retired"] is True
    assert body["enabled"] is False
    assert body["graph_id"] == graph_id


def test_a_freshly_minted_graph_reports_enabled_and_not_retired(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    graph_id = _mint(hub)

    detail = hub.client.get(f"/api/graphs/{graph_id}").json()

    assert detail["enabled"] is True
    assert detail["retired"] is False


def test_retire_does_not_change_the_immutable_graph_row(tmp_path: Path) -> None:
    """The graph's own structure (name, entry node, nodes, edges) is untouched by
    retiring it — retire is an append-only fact, never a mutation of ``graphs``."""
    hub = build_hub(tmp_path)
    graph_id = _mint(hub)
    before = hub.client.get(f"/api/graphs/{graph_id}").json()

    hub.client.post(f"/api/graphs/{graph_id}/retire", json={"by": "operator"})

    after = hub.client.get(f"/api/graphs/{graph_id}").json()
    assert after["name"] == before["name"]
    assert after["entry_node_id"] == before["entry_node_id"]
    assert after["nodes"] == before["nodes"]
    assert after["edges"] == before["edges"]


def test_enable_reverses_a_retire(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    graph_id = _mint(hub)
    hub.client.post(f"/api/graphs/{graph_id}/retire", json={"by": "operator"})

    resp = hub.client.post(f"/api/graphs/{graph_id}/enable", json={"by": "operator"})

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["retired"] is False
    assert body["enabled"] is True
    assert hub.client.get(f"/api/graphs/{graph_id}").json()["retired"] is False


def test_enable_on_a_never_retired_graph_is_a_harmless_no_op(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    graph_id = _mint(hub)

    resp = hub.client.post(f"/api/graphs/{graph_id}/enable", json={"by": "operator"})

    assert resp.status_code == 202, resp.text
    assert resp.json()["retired"] is False


def test_retire_unknown_graph_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/graphs/gr_does_not_exist/retire", json={"by": "operator"})
    assert resp.status_code == 404


def test_enable_unknown_graph_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/graphs/gr_does_not_exist/enable", json={"by": "operator"})
    assert resp.status_code == 404


def test_retire_defaults_by_to_operator(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    graph_id = _mint(hub)

    resp = hub.client.post(f"/api/graphs/{graph_id}/retire", json={})

    assert resp.status_code == 202, resp.text
    assert resp.json()["retired"] is True


# GET /api/graphs — effective falls back to the newest non-retired version; a
# fully-retired name marks none of its versions effective.


def test_retiring_the_newest_version_falls_effective_back_to_the_prior_one(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    old_id = _mint(hub)
    hub.clock.advance(timedelta(hours=1))
    new_id = _mint(hub)

    hub.client.post(f"/api/graphs/{new_id}/retire", json={"by": "operator"})

    rows = {row["graph_id"]: row for row in hub.client.get("/api/graphs").json()}
    assert rows[new_id]["retired"] is True
    assert rows[new_id]["effective"] is False
    assert rows[old_id]["retired"] is False
    assert rows[old_id]["effective"] is True


def test_retiring_every_version_of_a_name_marks_none_effective(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    old_id = _mint(hub)
    hub.clock.advance(timedelta(hours=1))
    new_id = _mint(hub)

    hub.client.post(f"/api/graphs/{old_id}/retire", json={"by": "operator"})
    hub.client.post(f"/api/graphs/{new_id}/retire", json={"by": "operator"})

    rows = {row["graph_id"]: row for row in hub.client.get("/api/graphs").json()}
    assert rows[old_id]["effective"] is False
    assert rows[new_id]["effective"] is False


def test_re_enabling_restores_effective(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    graph_id = _mint(hub)
    hub.client.post(f"/api/graphs/{graph_id}/retire", json={"by": "operator"})
    assert hub.client.get("/api/graphs").json()[0]["effective"] is False

    hub.client.post(f"/api/graphs/{graph_id}/enable", json={"by": "operator"})

    assert hub.client.get("/api/graphs").json()[0]["effective"] is True


# Retiring every version of the packaged *default* graph must survive a restart, not
# be silently undone by the next lazy `ensure_default` (issue #101).


def test_retiring_every_version_of_the_default_graph_survives_a_restart(tmp_path: Path) -> None:
    """Retiring the packaged default graph's only version is the operator's deliberate
    brake — not undone by a second ``ensure_default`` call across a hub restart (fresh
    ``HubServices``/engine, same on-disk store)."""
    hub = build_hub(tmp_path)
    doc = hub.services.default_graph_doc
    graph = hub.services.graph_mint.ensure_default(doc, definition_yaml=hub.services.default_graph_yaml)
    hub.services.graph_lifecycle.retire(graph, by="operator")
    assert hub.services.graphs.get_enabled_by_name(doc.name) is None

    # A second "boot": a fresh HubServices over the same sqlite file — never the same
    # Python objects the first boot already ran `ensure_default` against.
    restarted = build_hub(tmp_path)
    with pytest.raises(DefaultGraphRetired):
        restarted.services.graph_mint.ensure_default(
            restarted.services.default_graph_doc, definition_yaml=restarted.services.default_graph_yaml
        )
    # Only the one graph this test itself minted exists — the retire was not undone
    # by a silent re-mint racing ahead of the assertion above.
    same_name = [g for g in restarted.services.graphs.list_all() if g.name == doc.name]
    assert [g.graph_id for g in same_name] == [graph.graph_id]

    # The route-level refusal an operator actually hits: ingest against the retired
    # default surfaces 503, not a silent fresh mint.
    resp = restarted.client.post("/api/chunks", json={"tokens": ["default:1"]})
    assert resp.status_code == 503, resp.text
    assert doc.name in resp.json()["detail"]
