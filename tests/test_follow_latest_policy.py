"""The standing follow-latest migration policy (issue #164).

Two tiers: the precedence rule (`resolve_follow_latest`) is a pure function at the unit
tier; the policy's effect on a real transition is driven over the live HTTP surface at
the component tier.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from blizzard.hub.domain.graph import resolve_follow_latest
from blizzard.hub.domain.work import MigrationSource
from tests.support import HubHarness, build_hub, pointer_token, report_lease

_POINTER = {"source": "default", "ref": "9"}

# One graph name, minted more than once. `build -pass-> deliver` so a transition has a
# destination name for the newer mint to be matched on.
_YAML = """
name: {name}
entry: build
nodes:
  build:
    executor: runner
    prompt: {prompt}
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
          description: Done.
          to: done
"""

# A later mint of the same name whose `deliver` node is gone — the entry-node fallback.
_YAML_NO_DELIVER = """
name: {name}
entry: triage
nodes:
  triage:
    executor: runner
    prompt: Triage.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Done.
          to: done
"""


# --------------------------------------------------------------------------- #
# The precedence rule — pure, unit tier
# --------------------------------------------------------------------------- #


@pytest.mark.unit
@pytest.mark.parametrize(
    ("graph_policy", "hub_default", "expected"),
    [
        (None, False, False),  # inherit a hub that has not opted in — the shipped default
        (None, True, True),  # inherit a hub that has
        (True, False, True),  # graph overrides a false hub
        (False, True, False),  # graph overrides a true hub
        (True, True, True),
        (False, False, False),
    ],
)
def test_resolve_follow_latest_precedence(graph_policy: bool | None, hub_default: bool, expected: bool) -> None:
    assert resolve_follow_latest(graph_policy, hub_default=hub_default) is expected


# --------------------------------------------------------------------------- #
# The policy at a real transition — component tier
# --------------------------------------------------------------------------- #


def _mint(hub: HubHarness, yaml_text: str) -> str:
    resp = hub.client.post("/api/graphs", json={"definition_yaml": yaml_text})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["graph_id"])


def _set_policy(hub: HubHarness, graph_id: str, follow_latest: bool | None) -> httpx.Response:
    resp = hub.client.post(f"/api/graphs/{graph_id}/follow-latest", json={"follow_latest": follow_latest})
    assert resp.status_code == 202, resp.text
    return resp


def _claimed_chunk(hub: HubHarness) -> tuple[str, str]:
    """Ingest, promote and claim one chunk on the newest enabled graph. Returns
    ``(chunk_id, build_node_id)``."""
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    hub.client.post(f"/api/chunks/{chunk_id}/promote")
    node_id = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)
    return chunk_id, node_id


def _complete(hub: HubHarness, chunk_id: str, node_id: str, *, choice: str = "pass") -> httpx.Response:
    return hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": choice, "epoch": 1, "runner_id": "r1", "from_node_id": node_id, "artifacts": []},
    )


def _arm(hub: HubHarness, *, name: str = "default-delivery", newer: str | None = None) -> tuple[str, str, str]:
    """Mint v1, claim a chunk on it, then mint a newer v2 of the same name.

    Returns ``(chunk_id, build_node_id, v2_graph_id)``. The clock advances between the
    two mints so "newest" is unambiguous rather than a graph-id tie-break.
    """
    v1 = _mint(hub, _YAML.format(name=name, prompt="Build."))
    chunk_id, build_node = _claimed_chunk(hub)
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"] == v1
    hub.clock.advance(timedelta(minutes=1))
    v2 = _mint(hub, (newer or _YAML).format(name=name, prompt="Build, but better."))
    return chunk_id, build_node, v2


@pytest.mark.component
def test_a_policy_true_graph_repins_the_chunk_to_the_newest_mint(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node, v2 = _arm(hub)
    _set_policy(hub, hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"], True)

    resp = _complete(hub, chunk_id, build_node)

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "migrated"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == v2
    assert detail["current_node_name"] == "deliver"  # same-named landing on the destination
    facts = hub.services.chunks.load_facts(chunk_id)
    assert facts is not None
    # Recorded as a migration fact, never disguised as a transition.
    assert len(facts.migrations) == 1
    assert len(facts.transitions) == 0


@pytest.mark.component
def test_the_hub_default_arms_a_graph_that_says_nothing(tmp_path: Path) -> None:
    # The graph's tri-state is null (no policy fact at all — every mint's default), so
    # the hub-level setting decides.
    hub = build_hub(tmp_path, follow_latest=True)
    chunk_id, build_node, v2 = _arm(hub)

    assert _complete(hub, chunk_id, build_node).json()["outcome"] == "migrated"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"] == v2


@pytest.mark.component
def test_a_graph_saying_false_overrides_a_true_hub(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, follow_latest=True)
    chunk_id, build_node, _v2 = _arm(hub)
    pinned = hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"]
    _set_policy(hub, pinned, False)

    resp = _complete(hub, chunk_id, build_node)

    assert resp.json()["outcome"] != "migrated"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == pinned  # unchanged
    assert detail["current_node_name"] == "deliver"  # an ordinary transition still happened
    facts = hub.services.chunks.load_facts(chunk_id)
    assert facts is not None and len(facts.transitions) == 1 and len(facts.migrations) == 0


@pytest.mark.component
def test_the_shipped_default_leaves_every_chunk_pinned(tmp_path: Path) -> None:
    # `follow_latest = false` is the shipped hub default, so a newer mint moves nothing
    # unless someone opts in. This is the "did we change behavior by landing this" pin.
    hub = build_hub(tmp_path)
    chunk_id, build_node, _v2 = _arm(hub)
    pinned = hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"]

    assert _complete(hub, chunk_id, build_node).json()["outcome"] != "migrated"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"] == pinned


@pytest.mark.component
def test_an_explicit_intent_takes_precedence_over_the_policy(tmp_path: Path) -> None:
    # Both are armed and both would fire; the operator's own aim wins, and the chunk
    # lands on the *intent's* target rather than the newest same-name mint.
    hub = build_hub(tmp_path, follow_latest=True)
    chunk_id, build_node, v2 = _arm(hub)
    other = _mint(hub, _YAML.format(name="triage-delivery", prompt="Triage."))
    assert (
        hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": other}}).status_code == 202
    )

    assert _complete(hub, chunk_id, build_node).json()["outcome"] == "migrated"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == other
    assert detail["graph_id"] != v2
    assert detail["intended_migration"] is None  # the intent fired and cleared


@pytest.mark.component
def test_an_auto_intent_that_falls_through_still_blocks_the_policy(tmp_path: Path) -> None:
    """The precedence is "an intent exists", not "an intent fired": a falling-through
    `auto` intent still blocks the policy, and both stay set for next time."""
    hub = build_hub(tmp_path, follow_latest=True)
    chunk_id, build_node, _v2 = _arm(hub)
    pinned = hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"]
    no_match = _mint(hub, _YAML_NO_DELIVER.format(name="triage-delivery"))
    hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": no_match}})

    assert _complete(hub, chunk_id, build_node).json()["outcome"] != "migrated"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == pinned
    assert detail["intended_migration"] is not None  # still set for next time


@pytest.mark.component
def test_a_chunk_already_on_the_newest_mint_is_a_no_op(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, follow_latest=True)
    v1 = _mint(hub, _YAML.format(name="default-delivery", prompt="Build."))
    chunk_id, build_node = _claimed_chunk(hub)

    assert _complete(hub, chunk_id, build_node).json()["outcome"] != "migrated"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == v1
    facts = hub.services.chunks.load_facts(chunk_id)
    assert facts is not None and len(facts.transitions) == 1


@pytest.mark.component
def test_a_newer_mint_that_is_retired_moves_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, follow_latest=True)
    chunk_id, build_node, v2 = _arm(hub)
    pinned = hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"]
    assert hub.client.post(f"/api/graphs/{v2}/retire", json={"by": "op"}).status_code == 202

    assert _complete(hub, chunk_id, build_node).json()["outcome"] != "migrated"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"] == pinned


@pytest.mark.component
def test_the_policy_never_drags_a_chunk_backwards_onto_an_older_mint(tmp_path: Path) -> None:
    """The chunk's own mint is retired, so name resolution answers with an *older* one
    (`get_enabled_by_name` returns v1) — following latest must only move forward, never
    rewind onto it."""
    hub = build_hub(tmp_path, follow_latest=True)
    v1 = _mint(hub, _YAML.format(name="default-delivery", prompt="Build."))
    hub.clock.advance(timedelta(minutes=1))
    v2 = _mint(hub, _YAML.format(name="default-delivery", prompt="Build, but better."))
    chunk_id, build_node = _claimed_chunk(hub)
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"] == v2
    assert hub.client.post(f"/api/graphs/{v2}/retire", json={"by": "op"}).status_code == 202
    assert hub.services.graphs.get_enabled_by_name("default-delivery").graph_id == v1  # type: ignore[union-attr]

    assert _complete(hub, chunk_id, build_node).json()["outcome"] != "migrated"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"] == v2


@pytest.mark.component
def test_a_newer_mint_without_the_destination_node_lands_on_its_entry(tmp_path: Path) -> None:
    # The entry-node fallback (#124): unlike an `auto` intent, the policy has nothing
    # to stay set for, so it lands on the target's entry node instead of deferring.
    hub = build_hub(tmp_path, follow_latest=True)
    chunk_id, build_node, v2 = _arm(hub, newer=_YAML_NO_DELIVER)

    assert _complete(hub, chunk_id, build_node).json()["outcome"] == "migrated"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == v2
    assert detail["current_node_name"] == "triage"  # the target's entry node


# --------------------------------------------------------------------------- #
# The policy's own surface — the stored tri-state on GraphView
# --------------------------------------------------------------------------- #


@pytest.mark.component
def test_graph_view_exposes_the_stored_tri_state_and_round_trips_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    graph_id = _mint(hub, _YAML.format(name="default-delivery", prompt="Build."))

    # Every mint starts at `null` — inherit — with no policy fact at all.
    assert hub.client.get(f"/api/graphs/{graph_id}").json()["follow_latest"] is None

    for value in (True, False, None):
        assert _set_policy(hub, graph_id, value).json()["follow_latest"] is value
        assert hub.client.get(f"/api/graphs/{graph_id}").json()["follow_latest"] is value

    # Newest-fact-wins, and reverting to inherit is itself an appended fact rather than
    # a delete — a later read sees the newest value, not the first one written.
    _set_policy(hub, graph_id, True)
    assert hub.client.get(f"/api/graphs/{graph_id}").json()["follow_latest"] is True


@pytest.mark.component
def test_setting_the_policy_on_an_unknown_graph_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs/gr_nope/follow-latest", json={"follow_latest": True}).status_code == 404


@pytest.mark.component
def test_the_policy_and_the_retire_brake_are_independent(tmp_path: Path) -> None:
    # Separate fact tables, so a retire/re-enable cycle must not disturb the policy, nor
    # a policy write the retire state.
    hub = build_hub(tmp_path)
    graph_id = _mint(hub, _YAML.format(name="default-delivery", prompt="Build."))
    _set_policy(hub, graph_id, True)

    assert hub.client.post(f"/api/graphs/{graph_id}/retire", json={"by": "op"}).json()["follow_latest"] is True
    assert hub.client.post(f"/api/graphs/{graph_id}/enable", json={"by": "op"}).json()["follow_latest"] is True

    assert _set_policy(hub, graph_id, None).json()["retired"] is False


@pytest.mark.component
def test_the_terminal_transition_is_never_hijacked_by_the_policy(tmp_path: Path) -> None:
    """A chunk one submission from `done` must finish, not restart on the newer mint."""
    hub = build_hub(tmp_path, follow_latest=True)
    v1 = _mint(hub, _YAML.format(name="default-delivery", prompt="Build."))
    chunk_id, build_node = _claimed_chunk(hub)

    # build -pass-> deliver, while v1 is still the only mint: an ordinary transition.
    assert _complete(hub, chunk_id, build_node).json()["outcome"] != "migrated"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["current_node_name"] == "deliver"

    # A deploy mints v2 of the same name while the chunk sits one step from done.
    hub.clock.advance(timedelta(minutes=1))
    v2 = _mint(hub, _YAML.format(name="default-delivery", prompt="Build, but better."))
    assert v2 != v1

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    deliver_node = detail["current_node_id"]
    epoch = detail["latest_epoch"]
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": epoch, "runner_id": "r1", "from_node_id": deliver_node, "artifacts": []},
    )

    assert resp.json()["outcome"] == "done", resp.text
    final = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert final["status"] == "done"
    assert final["graph_id"] == v1, "a terminating chunk must not be re-pinned"
    facts = hub.services.chunks.load_facts(chunk_id)
    assert facts is not None and len(facts.migrations) == 0


@pytest.mark.component
def test_a_policy_migration_is_attributed_to_the_policy_in_history(tmp_path: Path) -> None:
    """The move is recorded as the policy's, not as an operator's — same-name alone
    isn't a tell, since `hub chunk migrate` by name also resolves to a newer mint."""
    hub = build_hub(tmp_path, follow_latest=True)
    chunk_id, build_node, _v2 = _arm(hub)

    assert _complete(hub, chunk_id, build_node).json()["outcome"] == "migrated"

    facts = hub.services.chunks.load_facts(chunk_id)
    assert facts is not None
    assert [m.source for m in facts.migrations] == [MigrationSource.FOLLOW_LATEST]
    migrations = hub.client.get(f"/api/chunks/{chunk_id}").json()["migrations"]
    assert [m["source"] for m in migrations] == ["follow-latest"]


@pytest.mark.component
def test_an_operator_intent_migration_is_attributed_to_the_intent(tmp_path: Path) -> None:
    """The discriminator tells the three paths apart — an explicit intent reads `intent`,
    so `follow-latest` genuinely identifies the unasked-for move rather than tagging
    every migration alike."""
    hub = build_hub(tmp_path)
    chunk_id, build_node, _v2 = _arm(hub)
    other = _mint(hub, _YAML.format(name="triage-delivery", prompt="Triage."))
    hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": other}})

    assert _complete(hub, chunk_id, build_node).json()["outcome"] == "migrated"

    migrations = hub.client.get(f"/api/chunks/{chunk_id}").json()["migrations"]
    assert [m["source"] for m in migrations] == ["intent"]
