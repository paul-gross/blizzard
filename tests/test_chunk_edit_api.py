"""The ``PATCH /chunks/{id}`` edit route over the HTTP surface (issue #124, in #104's
shape; issue #27's graph/model edit, admit set widened to ``ready``-unclaimed by #120).

A not-ready **or** ready-and-unclaimed chunk's workflow graph, model selection, and
migration intent are editable through one all-or-nothing ``PATCH``; the build fields are
refused (409) once the chunk is actually claimed (running, delivering, waiting_on_human,
needs_human, paused post-claim, done, stopped). The refusal itself (``EditService``) is
unit-tested in ``test_edit_service.py``; this file proves the controller wires it
correctly end to end — the read side (``graph_id``/``model`` on the list/detail views),
the write, the 404s, and the ``chunk-changed`` event. The edit/claim race itself (issue
#120's atomicity criterion) is proven at ``tests/test_edit_claim_race.py``, not here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.domain.work import DEFAULT_MODEL
from tests.support import build_hub, emitted_events, ingest

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "12"}

_ALT_YAML = """
name: alt-graph
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    judgement:
      prompt: |
        Assess the build.
      choices:
        pass:
          description: Complete and green.
          to: deliver
        fail:
          description: Incomplete.
          to: build
  deliver:
    executor: hub
    run:
      - command: "true"
    judgement:
      choices:
        success:
          description: Delivered.
          to: done
        failure:
          description: Failed to deliver.
          to: build
"""


def _mint_alt_graph(hub) -> str:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/graphs", json={"definition_yaml": _ALT_YAML})
    assert resp.status_code == 201, resp.text
    return resp.json()["graph_id"]


# --------------------------------------------------------------------------- #
# Read — graph_id/model already ride the list/detail views.
# --------------------------------------------------------------------------- #


def test_a_freshly_ingested_chunk_carries_the_default_graph_and_model(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "not_ready"
    assert detail["model"] == DEFAULT_MODEL
    default_graph_id = detail["graph_id"]

    summary = next(c for c in hub.client.get("/api/chunks").json() if c["chunk_id"] == chunk_id)
    assert summary["model"] == DEFAULT_MODEL
    assert summary["graph_id"] == default_graph_id


# --------------------------------------------------------------------------- #
# Write — PATCH /chunks/{id} (issue #124): graph_id/model/intended_migration
# applied all-or-nothing in one request, in #104's shape.
# --------------------------------------------------------------------------- #


def _claim(hub, chunk_id: str, *, runner_id: str = "r1") -> None:  # type: ignore[no-untyped-def]
    """Claim ``chunk_id`` for ``runner_id`` — the only status the plain #27/#120 graph and
    model edits refuse, and the status ``intended_migration``'s own window opens at."""
    resp = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": runner_id, "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert resp.status_code == 201, resp.text


def test_patch_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.patch("/api/chunks/ch_nope", json={"model": "claude-sonnet-4-5"})
    assert resp.status_code == 404


def test_patch_applies_graph_id_and_model_together(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)
    alt_graph_id = _mint_alt_graph(hub)

    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"graph_id": alt_graph_id, "model": "claude-sonnet-4-5"})

    assert resp.status_code == 202, resp.text
    assert resp.json() == {
        "chunk_id": chunk_id,
        "graph_id": alt_graph_id,
        "model": "claude-sonnet-4-5",
        "intended_migration": None,
    }
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == alt_graph_id
    assert detail["model"] == "claude-sonnet-4-5"


def test_patch_unknown_graph_id_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)

    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"graph_id": "gr_nope"})

    assert resp.status_code == 404


def test_patch_blank_model_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)

    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"model": "   "})

    assert resp.status_code == 422, resp.text
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["model"] == DEFAULT_MODEL


def test_patch_refuses_a_field_not_editable_at_the_current_status_and_writes_nothing(tmp_path: Path) -> None:
    """A mixed body refused on one field applies neither (issue #124's all-or-nothing
    redesign) — `graph_id` stays sealed once claimed even though a plain PATCH-only
    `intended_migration` body would be admitted at the same status."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    alt_graph_id = _mint_alt_graph(hub)
    _claim(hub, chunk_id)
    before = hub.client.get(f"/api/chunks/{chunk_id}").json()

    resp = hub.client.patch(
        f"/api/chunks/{chunk_id}",
        json={"graph_id": alt_graph_id, "intended_migration": {"to_graph": alt_graph_id}},
    )

    assert resp.status_code == 409, resp.text
    assert "graph_id" in resp.json()["detail"]
    after = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert after["graph_id"] == before["graph_id"]
    assert after["intended_migration"] is None


def test_patch_retired_graph_id_target_is_not_bypassed_by_a_different_valid_migration_target(tmp_path: Path) -> None:
    """A retired `graph_id` target must 409 on its own retirement even when the same
    request's `intended_migration.to_graph` names a different, non-retired graph — the
    two targets are resolved and validated independently, so a retired `graph_id`
    never slips past under cover of a valid migration target and nothing applies
    (pre-push review, issue #124)."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])  # promote=True by default -> ready
    retired_graph_id = _mint_alt_graph(hub)
    other_graph_id = _mint_alt_graph(hub)
    retire = hub.client.post(f"/api/graphs/{retired_graph_id}/retire", json={"by": "operator"})
    assert retire.status_code == 202, retire.text
    before = hub.client.get(f"/api/chunks/{chunk_id}").json()

    resp = hub.client.patch(
        f"/api/chunks/{chunk_id}",
        json={"graph_id": retired_graph_id, "intended_migration": {"to_graph": other_graph_id}},
    )

    assert resp.status_code == 409, resp.text
    assert "retired" in resp.json()["detail"]
    assert retired_graph_id in resp.json()["detail"]
    after = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert after["graph_id"] == before["graph_id"]
    assert after["intended_migration"] is None


def test_patch_applies_graph_id_and_a_different_intended_migration_target_together(tmp_path: Path) -> None:
    """The happy combined case: a `graph_id` repin and an `intended_migration` naming a
    *different* target graph both validate and apply together in one PATCH."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])  # promote=True by default -> ready
    new_graph_id = _mint_alt_graph(hub)
    migration_graph_id = _mint_alt_graph(hub)

    resp = hub.client.patch(
        f"/api/chunks/{chunk_id}",
        json={"graph_id": new_graph_id, "intended_migration": {"to_graph": migration_graph_id}},
    )

    assert resp.status_code == 202, resp.text
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == new_graph_id
    assert detail["intended_migration"]["graph_id"] == migration_graph_id


def test_patch_publishes_chunk_changed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)
    since = hub.events.latest_id()

    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"model": "claude-sonnet-4-5"})

    assert resp.status_code == 202, resp.text
    events = emitted_events(hub, since=since)
    assert "chunk-changed" in [e["event"] for e in events]


# --------------------------------------------------------------------------- #
# Write — PATCH intended_migration (issue #124).
# --------------------------------------------------------------------------- #


def test_get_chunk_intended_migration_is_null_by_default(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()

    assert detail["intended_migration"] is None


def test_patch_sets_an_auto_intended_migration_on_a_claimed_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    alt_graph_id = _mint_alt_graph(hub)
    _claim(hub, chunk_id)

    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": alt_graph_id}})

    assert resp.status_code == 202, resp.text
    expected = {"mode": "auto", "graph_id": alt_graph_id, "graph_name": "alt-graph", "node_name": None}
    assert resp.json()["intended_migration"] == expected
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["intended_migration"] == expected
    assert detail["status"] == "running"


def test_patch_sets_a_forced_intended_migration(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    alt_graph_id = _mint_alt_graph(hub)
    _claim(hub, chunk_id)

    resp = hub.client.patch(
        f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": alt_graph_id, "node": "deliver"}}
    )

    assert resp.status_code == 202, resp.text
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["intended_migration"] == {
        "mode": "forced",
        "graph_id": alt_graph_id,
        "graph_name": "alt-graph",
        "node_name": "deliver",
    }


def test_patch_resolves_a_graph_name_to_the_newest_enabled_graph_and_stores_its_id(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    alt_graph_id = _mint_alt_graph(hub)
    _claim(hub, chunk_id)

    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": "alt-graph"}})

    assert resp.status_code == 202, resp.text
    assert resp.json()["intended_migration"]["graph_id"] == alt_graph_id


def test_patch_overwrites_an_existing_intended_migration(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    alt_graph_id = _mint_alt_graph(hub)
    _claim(hub, chunk_id)
    first = hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": alt_graph_id}})
    assert first.status_code == 202, first.text

    resp = hub.client.patch(
        f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": alt_graph_id, "node": "deliver"}}
    )

    assert resp.status_code == 202, resp.text
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["intended_migration"]["mode"] == "forced"
    assert detail["intended_migration"]["node_name"] == "deliver"


def test_patch_clears_an_intended_migration_via_explicit_null(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    alt_graph_id = _mint_alt_graph(hub)
    _claim(hub, chunk_id)
    set_resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": alt_graph_id}})
    assert set_resp.status_code == 202, set_resp.text

    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": None})

    assert resp.status_code == 202, resp.text
    assert resp.json()["intended_migration"] is None
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["intended_migration"] is None


def test_patch_with_intended_migration_field_absent_leaves_it_unchanged(tmp_path: Path) -> None:
    """`intended_migration`'s window spans `ready` too, unlike `model`'s — set on a
    ready-unclaimed chunk here so a later PATCH naming only `model` (still editable at
    `ready`) can prove the absent field survives untouched."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])  # promote=True by default -> ready
    alt_graph_id = _mint_alt_graph(hub)
    set_resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": alt_graph_id}})
    assert set_resp.status_code == 202, set_resp.text

    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"model": "claude-sonnet-4-5"})

    assert resp.status_code == 202, resp.text
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["intended_migration"]["graph_id"] == alt_graph_id
    assert detail["model"] == "claude-sonnet-4-5"


def test_patch_intended_migration_refuses_once_the_chunk_is_stopped(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)
    alt_graph_id = _mint_alt_graph(hub)
    stop = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"})
    assert stop.status_code == 202, stop.text

    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": alt_graph_id}})

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "intended_migration" in detail
    assert "stopped" in detail


def test_patch_intended_migration_refuses_a_retired_target(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    alt_graph_id = _mint_alt_graph(hub)
    _claim(hub, chunk_id)
    retire = hub.client.post(f"/api/graphs/{alt_graph_id}/retire", json={"by": "operator"})
    assert retire.status_code == 202, retire.text

    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": alt_graph_id}})

    assert resp.status_code == 409, resp.text
    assert "retired" in resp.json()["detail"]
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["intended_migration"] is None


def test_patch_intended_migration_refuses_the_chunks_own_current_pin(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    _claim(hub, chunk_id)
    own_graph_id = hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"]

    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": own_graph_id}})

    assert resp.status_code == 409, resp.text
    assert "current graph pin" in resp.json()["detail"]


def test_patch_intended_migration_forced_refuses_a_node_absent_from_the_target(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    alt_graph_id = _mint_alt_graph(hub)
    _claim(hub, chunk_id)

    resp = hub.client.patch(
        f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": alt_graph_id, "node": "nope"}}
    )

    assert resp.status_code == 409, resp.text
    assert "nope" in resp.json()["detail"]
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["intended_migration"] is None


def test_patch_intended_migration_blank_to_graph_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    _claim(hub, chunk_id)

    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": "   "}})

    assert resp.status_code == 422, resp.text


def test_patch_intended_migration_blank_node_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    alt_graph_id = _mint_alt_graph(hub)
    _claim(hub, chunk_id)

    resp = hub.client.patch(
        f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": alt_graph_id, "node": "   "}}
    )

    assert resp.status_code == 422, resp.text


def test_patch_intended_migration_unknown_graph_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    _claim(hub, chunk_id)

    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": "gr_nope"}})

    assert resp.status_code == 404, resp.text
