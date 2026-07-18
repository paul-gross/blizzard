"""Ingest, the live-pointer conflict, and the ready-queue peek (component tier)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.hub.domain.work import PmPointer
from tests.support import FakePmSource, build_hub, ingest, pointer_token, write_chunk_pause_facts

pytestmark = pytest.mark.component

_P1 = {"source": "default", "ref": "1"}
_P2 = {"source": "default", "ref": "2"}

# A build -> review -> deliver graph mirroring the packaged default's shape, but with a
# trivial `run: [{command: "true"}]` deliver node instead of the packaged script that
# talks to a real forge over HTTP — this test drives re-ingest-after-terminal, not
# delivery mechanics, so it stays hermetic (no live forge needed).
_BUILD_REVIEW_DELIVER_YAML = """
name: default-delivery
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
          to: review
        fail:
          description: Incomplete.
          to: build
  review:
    executor: runner
    prompt: |
      Review the change.
    judgement:
      prompt: |
        Assess the review.
      choices:
        pass:
          description: Passes review.
          to: deliver
        fail:
          description: Blocking issues.
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


def test_ingest_mints_a_chunk_pinned_to_the_default_graph(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_P1)]})
    assert resp.status_code == 201
    chunk_id = resp.json()["chunk_id"]
    assert chunk_id.startswith("ch_")

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "not_ready"  # rests not-ready until promoted
    assert detail["pm_pointers"] == [
        {**_P1, "label": "default#1", "web_url": "http://forge.local/acme/widget/issues/1"}
    ]
    # The default graph was minted on first ingest and the chunk pinned to it.
    graphs = hub.services.graphs.list_all()
    assert [g.name for g in graphs] == ["default-delivery"]
    assert detail["graph_id"] == graphs[0].graph_id


def test_ingest_batches_multiple_pointers_into_one_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_P1), pointer_token(_P2)]})
    assert resp.status_code == 201
    detail = hub.client.get(f"/api/chunks/{resp.json()['chunk_id']}").json()
    assert detail["pm_pointers"] == [
        {**_P1, "label": "default#1", "web_url": "http://forge.local/acme/widget/issues/1"},
        {**_P2, "label": "default#2", "web_url": "http://forge.local/acme/widget/issues/2"},
    ]


def test_list_row_is_board_legible(tmp_path: Path) -> None:
    # The fleet list resolves the current node's human name and each pointer's
    # `{source}#{ref}` label server-side, so the board renders `build` and `default#1`
    # without reassembly. A pointer naming no configured source degrades
    # to a null label/web_url rather than erroring — minted straight through the domain
    # service (ingest's 422 already rejects an unconfigured source at the front door, so
    # a board row carrying one can only arise from a chunk minted before its source was
    # dropped from config).
    hub = build_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_P1)]}).json()["chunk_id"]
    graph = hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )
    unconfigured = PmPointer(source="retired", ref="9")
    unconfigured_id = hub.services.ingest.ingest([unconfigured], graph=graph)

    rows = {r["chunk_id"]: r for r in hub.client.get("/api/chunks").json()}
    assert rows[chunk_id]["current_node_name"] == "build"  # the entry node, pre-first-transition
    assert rows[chunk_id]["pm_pointers"] == [
        {**_P1, "label": "default#1", "web_url": "http://forge.local/acme/widget/issues/1"}
    ]
    assert rows[unconfigured_id]["pm_pointers"] == [{"source": "retired", "ref": "9", "label": None, "web_url": None}]


def test_ingest_rests_not_ready_and_promote_makes_it_claimable(tmp_path: Path) -> None:
    # Ingest mints not-ready: visible on the fleet list, absent from the ready queue,
    # so no runner claims it. Promoting flips it to ready and admits it to the queue.
    hub = build_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_P1)]}).json()["chunk_id"]
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "not_ready"
    assert [r["chunk_id"] for r in hub.client.get("/api/chunks").json()] == [chunk_id]  # on the board
    assert hub.client.get("/api/queue/peek").json()["entries"] == []  # never claimed

    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    assert [e["chunk_id"] for e in hub.client.get("/api/queue/peek").json()["entries"]] == [chunk_id]


def test_promote_is_idempotent_and_404s_unknown_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_P1)]}).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    # A second promote is a harmless no-op — still ready, still one queue entry.
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    assert len(hub.client.get("/api/queue/peek").json()["entries"]) == 1
    assert hub.client.post("/api/chunks/ch_nope/promote").status_code == 404


def test_live_pointer_reingest_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_P1)]}).json()["chunk_id"]

    conflict = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_P1)]})
    assert conflict.status_code == 409
    body = conflict.json()
    assert body["existing_chunk_id"] == first
    assert body["source"] == _P1["source"]
    assert body["ref"] == _P1["ref"]


def test_a_paused_chunk_still_holds_its_pointer_live(tmp_path: Path) -> None:
    """Pausing must not read as terminal (issue #46): ``_TERMINAL`` stays ``{stopped, done}``.

    The live-pointer conflict is keyed on the holder being non-terminal, so admitting
    ``paused`` to ``_TERMINAL`` would let this re-ingest mint a **second** chunk for the same
    issue — two chunks racing one pointer, from an operator merely pressing pause. Nothing
    else pins that: pause is a new status and every other test predates it.
    """
    hub = build_hub(tmp_path)
    first = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_P1)]}).json()["chunk_id"]
    write_chunk_pause_facts(tmp_path, first, (True, hub.clock.now()))
    assert hub.client.get(f"/api/chunks/{first}").json()["status"] == "paused"

    conflict = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_P1)]})
    assert conflict.status_code == 409, "a paused chunk still holds its pointer — no duplicate mint"
    assert conflict.json()["existing_chunk_id"] == first


# --------------------------------------------------------------------------- #
# Ingest-time source resolution — the 422 rejection and the name-keyed
# lookup two configured sources need.
# --------------------------------------------------------------------------- #


def test_ingest_rejects_a_token_no_configured_source_claims(tmp_path: Path) -> None:
    """A token no configured binding's ``parse`` claims is a 422, naming the token and
    what is configured."""
    hub = build_hub(tmp_path, pm={"widget": FakePmSource(name="widget", repo="acme/widget")})
    other = {"source": "other", "ref": "1"}

    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(other)]})

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "other" in detail
    assert "widget" in detail
    # The whole request rejects together — nothing was minted.
    assert hub.client.get("/api/chunks").json() == []


def test_ingest_succeeds_when_a_configured_source_claims_the_pointer(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, pm={"widget": FakePmSource(name="widget", repo="acme/widget")})
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token({"source": "widget", "ref": "1"})]})
    assert resp.status_code == 201, resp.text


def test_resolver_picks_the_matching_source_when_two_are_configured(tmp_path: Path) -> None:
    """A pointer resolves to its own named binding by ``registry.get(pointer.source)``
     — the fetch, and the label it renders, must come from ``beta``'s
    binding, not ``alpha``'s, even though ``alpha`` is registered first."""
    alpha = FakePmSource(name="alpha", repo="acme/alpha")
    beta = FakePmSource(name="beta", repo="acme/beta")
    hub = build_hub(tmp_path, pm={"alpha": alpha, "beta": beta})
    beta_pointer = {"source": "beta", "ref": "7"}

    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(beta_pointer)]}).json()["chunk_id"]

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["pm_pointers"] == [
        {**beta_pointer, "label": "beta#7", "web_url": "http://forge.local/acme/beta/issues/7"}
    ]
    # The fetch went to the right binding too — not `alpha`'s.
    items = hub.client.get(f"/api/chunks/{chunk_id}/pm-items").json()["items"]
    assert items[0]["label"] == "beta#7"
    assert items[0]["error"] is None
    assert beta.fetched == ["7"]
    assert alpha.fetched == []


def test_pm_items_503s_when_no_pm_source_is_configured_at_all(tmp_path: Path) -> None:
    """An explicitly empty registry is a legal, PM-reach-free hub — pm-items 503s
    up front rather than 422ing at ingest, since an empty registry names no source at all."""
    hub = build_hub(tmp_path, pm={})

    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_P1)]})
    assert resp.status_code == 422, resp.text  # no source at all also can't claim it

    # Mint the degenerate chunk straight through the domain service (bypassing the route's
    # 422) to exercise the pm-items 503 the same way test_pm_items_with_no_pointers_is_an_
    # empty_list mints its own degenerate fixture.
    graph = hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )
    chunk_id = hub.services.ingest.ingest([PmPointer(source=_P1["source"], ref=_P1["ref"])], graph=graph)
    items = hub.client.get(f"/api/chunks/{chunk_id}/pm-items")
    assert items.status_code == 503
    assert items.json()["detail"] == "no PM work-source is configured"


def _pass(hub, chunk_id: str, node_id: str, epoch: int, *, artifacts: list[dict]) -> dict:  # type: ignore[no-untyped-def]
    return hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": epoch,
            "runner_id": "r1",
            "from_node_id": node_id,
            "artifacts": artifacts,
        },
    ).json()


def test_terminal_pointer_reingest_mints_a_fresh_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    # A build -> review -> deliver graph pre-minted under the packaged default's own
    # name, so `ensure_default` (POST /chunks) reuses it by name (D-081) instead of
    # minting the packaged prose graph — this test drives its own terminal chunk, not
    # the packaged default's real forge-talking delivery script.
    assert hub.client.post("/api/graphs", json={"definition_yaml": _BUILD_REVIEW_DELIVER_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_P1)]}).json()["chunk_id"]
    # Drive the chunk terminal through the default build -> review -> deliver graph.
    build_id = hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    commit = [{"name": "w", "kind": "git_commit", "repo": "acme/widget", "branch_name": "b", "commit_hash": "c"}]
    to_review = _pass(hub, chunk_id, build_id, 1, artifacts=commit)
    review_id = to_review["next_envelope"]["node"]["node_id"]
    # Report the review node-step's fresh lease so the hub's fence tracks it.
    assert hub.client.post(f"/api/chunks/{chunk_id}/leases", json={"epoch": 2, "runner_id": "r1"}).status_code == 202
    _pass(hub, chunk_id, review_id, 2, artifacts=[])
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"

    # Re-ingesting the same pointer once every prior holder is terminal is legal.
    again = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_P1)]})
    assert again.status_code == 201
    assert again.json()["chunk_id"] != chunk_id


def test_queue_peek_lists_ready_chunks_fifo_and_hides_claimed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = ingest(hub, [_P1])  # ingest + promote → ready and in the queue
    hub.clock.advance(timedelta(seconds=1))  # a distinct, later mint time for FIFO ordering
    second = ingest(hub, [_P2])

    entries = hub.client.get("/api/queue/peek").json()["entries"]
    assert [e["chunk_id"] for e in entries] == [first, second]
    assert [e["position"] for e in entries] == [0, 1]

    # Claiming the first removes it from the ready queue.
    hub.client.post(
        "/api/routes",
        json={"chunk_id": first, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    remaining = hub.client.get("/api/queue/peek").json()["entries"]
    assert [e["chunk_id"] for e in remaining] == [second]
