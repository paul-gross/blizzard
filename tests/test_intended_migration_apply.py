"""The transition-time consult of a chunk's standing migration intent (issue #124,
Phase 4) — `hub/domain/apply.py`'s shared consult helper, wired at both common-apply-
path transition sites, plus the `submit_completion` controller resolution
(`hub/api/fleet.py`).

Component tier over the real HTTP surface, mirroring `test_migration_apply.py`'s shape
for #90: a claimed chunk's `PATCH .../intended_migration` sets the intent, an ordinary
completion's transition either fires it (recording a `chunk_migrations` fact instead of
a `transitions` row, re-pinning the graph, clearing the intent) or falls through
unchanged (`auto` with no destination-name match). Unlike #90's migration edge, the
transition itself never names the target graph — the intent does, set out of band.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from tests.support import build_hub, pointer_token, report_lease

_POINTER = {"source": "default", "ref": "9"}

# A source graph with no cross-graph edges at all — every transition here is ordinary;
# the migration intent (set out of band via PATCH) is what makes one of them migrate.
# Three runner nodes so the "auto, no match" case can be proven across two transitions:
# the first (`build` -pass-> `deliver`) leaves the intent set, the second
# (`deliver` -pass-> `ship`) fires it.
_SRC_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Ready.
          to: deliver
        fail:
          description: Retry.
          to: build
  deliver:
    executor: runner
    prompt: Deliver.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Ready to ship.
          to: ship
        fail:
          description: Retry.
          to: deliver
  ship:
    executor: runner
    prompt: Ship.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Done.
          to: done
        fail:
          description: Retry.
          to: ship
"""

# A migration target carrying both `deliver` and `ship` node names — the simple
# auto-match and forced tests land on whichever name the test names.
_TARGET_YAML = """
name: triage
entry: build
nodes:
  build:
    executor: runner
    prompt: Triage build.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Ready.
          to: deliver
  deliver:
    executor: runner
    prompt: Triage deliver.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Done.
          to: done
        fail:
          description: Retry.
          to: deliver
  ship:
    executor: runner
    prompt: Triage ship.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Done.
          to: done
        fail:
          description: Retry.
          to: ship
"""

# A migration target carrying `ship` but NOT `deliver` — the auto-no-match-then-match
# test's target: the first transition's destination (`deliver`) doesn't exist here (no
# match, intent stays set), the second (`ship`) does (fires).
_TARGET_NO_DELIVER_YAML = """
name: triage-nomatch
entry: build
nodes:
  build:
    executor: runner
    prompt: Triage build.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Ready.
          to: ship
  ship:
    executor: runner
    prompt: Triage ship.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Done.
          to: done
        fail:
          description: Retry.
          to: ship
"""

# A migration target whose `deliver` node (the auto-match name) is hub-executed (issue
# #111) — mirrors `test_migration_apply.py`'s `_HUB_TARGET_YAML`: `success` routes
# onward to a non-terminal runner node so the retained route is what the test observes,
# not the terminal chunk's own release.
_TARGET_HUB_YAML = """
name: triage-hub
entry: build
nodes:
  build:
    executor: runner
    prompt: Triage build.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Ready.
          to: deliver
  deliver:
    executor: hub
    run:
      - command: "true"
    judgement:
      choices:
        success:
          description: Delivered.
          to: review
        failure:
          description: Failed to deliver.
          to: deliver
  review:
    executor: runner
    prompt: Review the delivery.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Done.
          to: done
        fail:
          description: Retry.
          to: deliver
"""

# A gate-source graph whose human gate's resolved choice is a plain (never `graph:`)
# transition to `deliver` — the resolving completion is where the gate-resolution
# consult site fires when an intent is standing.
_GATE_SRC_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Ready for signoff.
          to: approve-gate
        fail:
          description: Retry.
          to: build
  approve-gate:
    executor: runner
    judgement:
      by: human
      choices:
        approve:
          description: Hand off.
          to: deliver
        reject:
          description: Send back.
          to: build
  deliver:
    executor: runner
    prompt: Deliver.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Done.
          to: done
"""


def _mint(hub, yaml: str) -> str:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/graphs", json={"definition_yaml": yaml})
    assert resp.status_code == 201, resp.text
    return resp.json()["graph_id"]


def _setup(hub, *, src_yaml: str = _SRC_YAML) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    """Mint the source graph, ingest + promote + claim a chunk on it. Returns
    ``(chunk_id, build_node_id)``."""
    assert hub.client.post("/api/graphs", json={"definition_yaml": src_yaml}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    hub.client.post(f"/api/chunks/{chunk_id}/promote")
    node_id = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)
    return chunk_id, node_id


def _set_intent(hub, chunk_id: str, *, to_graph: str, node: str | None = None) -> httpx.Response:  # type: ignore[no-untyped-def]
    body = {"to_graph": to_graph}
    if node is not None:
        body["node"] = node
    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": body})
    assert resp.status_code == 202, resp.text
    return resp


def _complete(  # type: ignore[no-untyped-def]
    hub, chunk_id: str, node_id: str, *, choice: str = "pass", epoch: int = 1, artifacts: list | None = None
) -> httpx.Response:
    return hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": choice,
            "epoch": epoch,
            "runner_id": "r1",
            "from_node_id": node_id,
            "artifacts": [] if artifacts is None else artifacts,
        },
    )


def test_auto_name_match_migrates(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node = _setup(hub)
    triage_id = _mint(hub, _TARGET_YAML)
    _set_intent(hub, chunk_id, to_graph=triage_id)

    resp = _complete(hub, chunk_id, build_node, artifacts=[{"name": "notes", "kind": "asset", "content": "hi"}])

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "migrated"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == triage_id  # re-pinned to the intent's target
    assert detail["status"] == "ready"  # re-queued, claimable
    assert detail["current_node_name"] == "deliver"  # name-match landing on the destination
    assert detail["intended_migration"] is None  # cleared
    assert any(a["name"] == "notes" for a in detail["artifacts"])  # the step's artifacts carried
    facts = hub.services.chunks.load_facts(chunk_id)
    assert facts is not None
    assert len(facts.migrations) == 1
    assert len(facts.transitions) == 0  # a migration, never a transitions row


def test_auto_no_match_leaves_intent_set_across_two_transitions(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node = _setup(hub)
    nomatch_id = _mint(hub, _TARGET_NO_DELIVER_YAML)
    _set_intent(hub, chunk_id, to_graph=nomatch_id)

    # First transition: build -pass-> deliver. The target has no `deliver` node — no
    # match, the transition applies unchanged, and the intent stays set.
    first = _complete(hub, chunk_id, build_node)
    assert first.status_code == 200, first.text
    assert first.json()["outcome"] == "next"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] != nomatch_id  # never migrated
    assert detail["current_node_name"] == "deliver"  # ordinary transition landed here
    assert detail["intended_migration"] is not None  # still set
    facts = hub.services.chunks.load_facts(chunk_id)
    assert facts is not None
    assert len(facts.migrations) == 0
    assert len(facts.transitions) == 1

    # Second transition: deliver -pass-> ship. The target DOES have a `ship` node — fires.
    deliver_node = detail["current_node_id"]
    second = _complete(hub, chunk_id, deliver_node)
    assert second.status_code == 200, second.text
    assert second.json()["outcome"] == "migrated"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == nomatch_id
    assert detail["current_node_name"] == "ship"
    assert detail["intended_migration"] is None
    facts = hub.services.chunks.load_facts(chunk_id)
    assert facts is not None
    assert len(facts.migrations) == 1
    assert len(facts.transitions) == 1  # still just the one ordinary transition from before


def test_forced_migrates_to_the_named_node_regardless_of_destination(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node = _setup(hub)
    triage_id = _mint(hub, _TARGET_YAML)
    # Forced onto `ship`, even though this transition's own destination is `deliver`.
    _set_intent(hub, chunk_id, to_graph=triage_id, node="ship")

    resp = _complete(hub, chunk_id, build_node)

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "migrated"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == triage_id
    assert detail["current_node_name"] == "ship"  # the forced target, not `deliver`
    assert detail["intended_migration"] is None


def test_hub_executed_landing_retains_the_route_and_derives_delivering(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node = _setup(hub)
    hub_target_id = _mint(hub, _TARGET_HUB_YAML)
    _set_intent(hub, chunk_id, to_graph=hub_target_id)  # auto: matches `deliver`

    resp = _complete(hub, chunk_id, build_node)

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "hub_node_taken"  # not "migrated" — the runner keeps holding
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == hub_target_id
    assert detail["status"] == "running"  # retained route, landed hub node ran onward to `review`
    assert detail["current_node_name"] == "review"
    assert detail["intended_migration"] is None

    # The observable consequence of a retained route: a fresh claim loses the race.
    conflict = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r2", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert conflict.status_code == 409, conflict.text


def test_runner_landing_releases_the_route_and_requeues_ready(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node = _setup(hub)
    triage_id = _mint(hub, _TARGET_YAML)
    _set_intent(hub, chunk_id, to_graph=triage_id)

    resp = _complete(hub, chunk_id, build_node)

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "migrated"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "ready"

    # Claimable again — the route was released, not retained.
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r2", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    assert claim.json()["envelope"]["node"]["node_id"] == detail["current_node_id"]


def test_no_epoch_bump_at_migration_time_and_the_submitting_attempt_completes(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node = _setup(hub)
    triage_id = _mint(hub, _TARGET_YAML)
    _set_intent(hub, chunk_id, to_graph=triage_id)

    resp = _complete(hub, chunk_id, build_node, epoch=1)

    # The submitting attempt's own verdict (epoch=1) is accepted normally — no rejection,
    # no fresh epoch minted as part of the migration itself.
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "migrated"
    facts = hub.services.chunks.load_facts(chunk_id)
    assert facts is not None
    assert len(facts.migrations) == 1
    assert facts.migrations[0].epoch == 1  # the submitting epoch, not a bumped one
    assert len(facts.leases) == 1  # no lease minted by the migration write itself


def test_forced_target_retired_at_consult_is_skipped(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node = _setup(hub)
    triage_id = _mint(hub, _TARGET_YAML)
    _set_intent(hub, chunk_id, to_graph=triage_id, node="ship")
    retire = hub.client.post(f"/api/graphs/{triage_id}/retire", json={"by": "operator"})
    assert retire.status_code == 202, retire.text

    resp = _complete(hub, chunk_id, build_node)

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "next"  # the ordinary transition applied, unchanged
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] != triage_id  # never migrated
    assert detail["current_node_name"] == "deliver"  # the transition's own destination
    assert detail["intended_migration"] is not None  # left set — the operator can cancel/re-aim
    assert detail["intended_migration"]["node_name"] == "ship"
    facts = hub.services.chunks.load_facts(chunk_id)
    assert facts is not None
    assert len(facts.migrations) == 0


def test_gate_resolution_site_consults_the_intent_and_closes_the_decision(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _GATE_SRC_YAML}).status_code == 201
    triage_id = _mint(hub, _TARGET_YAML)  # carries a `deliver` node — the gate's own destination

    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    hub.client.post(f"/api/chunks/{chunk_id}/promote")
    build_node = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)
    _set_intent(hub, chunk_id, to_graph=triage_id)

    # build passes -> lands on the human gate; a decision opens.
    hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": build_node, "artifacts": []},
    )
    parked = hub.client.get(f"/api/chunks/{chunk_id}").json()
    decision_id = parked["decision"]["decision_id"]
    gate_node = parked["current_node_id"]

    # A person approves; the resolving completion's destination (`deliver`) matches the
    # target's own `deliver` node — the gate-resolution consult site fires the intent.
    assert hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "approve"}).status_code == 200
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "approve",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": gate_node,
            "decision_id": decision_id,
            "artifacts": [],
        },
    )

    assert resp.json()["outcome"] == "migrated", resp.text
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == triage_id
    assert detail["current_node_name"] == "deliver"
    # The gate's decision is closed — a migration writes no transitions row, so without
    # threading decision_id through this would stay a live phantom decision.
    assert detail["decision"] is None
    closed = hub.services.chunks.get_decision(decision_id)
    assert closed is not None and closed.transitioned is True


def test_a_replayed_intended_migration_completion_rederives_its_outcome(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node = _setup(hub)
    triage_id = _mint(hub, _TARGET_YAML)
    _set_intent(hub, chunk_id, to_graph=triage_id)

    first = _complete(hub, chunk_id, build_node)
    assert first.json()["outcome"] == "migrated"

    # A re-flushed completion (lost ack) replays to MIGRATED without a second re-pin.
    second = _complete(hub, chunk_id, build_node)
    assert second.status_code == 200, second.text
    assert second.json()["outcome"] == "migrated"

    facts = hub.services.chunks.load_facts(chunk_id)
    assert facts is not None
    assert len(facts.migrations) == 1  # idempotent — the natural key guards a double submit


def test_a_replayed_hub_landing_intended_migration_returns_hub_node_taken(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node = _setup(hub)
    hub_target_id = _mint(hub, _TARGET_HUB_YAML)
    _set_intent(hub, chunk_id, to_graph=hub_target_id)

    first = _complete(hub, chunk_id, build_node)
    assert first.json()["outcome"] == "hub_node_taken"

    second = _complete(hub, chunk_id, build_node)
    assert second.status_code == 200, second.text
    assert second.json()["outcome"] == "hub_node_taken"  # never "migrated" on replay (#111)

    facts = hub.services.chunks.load_facts(chunk_id)
    assert facts is not None
    assert len(facts.migrations) == 1
