"""Live/reconstructed parity of the activity feed (component tier).

Each fact source's live path is driven over the real routes (the hub node's through the flow driver), then
``GET /api/activity`` is rebuilt and compared with the ``chunk-changed`` frames the broker recorded: the same
``(key, cause)`` pairs on both sides. Keyless frames are outside the comparison.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.hub_event_types import HubEventType
from blizzard.hub.domain.chunk.model import RouteReleaseCause
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_events_store import EVENT_SOURCES
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    emitted_events,
    hub_scoped,
    pointer_token,
    report_escalation,
    report_lease,
    report_question,
)

_RUNNER = "r1"

_BUILD_THEN_HUB_NODE = """
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
          description: Done.
          to: deliver
  deliver:
    executor: hub
    action: pass
    judgement:
      choices:
        success:
          description: Delivered.
          to: done
        failure:
          description: Failed.
          to: build
"""

_MIGRATING = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build.
    judgement:
      prompt: Assess.
      choices:
        migrate:
          description: Hand off.
          to: graph:triage
"""

_MIGRATION_TARGET = """
name: triage
entry: build
nodes:
  build:
    executor: runner
    prompt: Triage.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Done.
          to: done
"""


def _mint(hub: HubHarness, ref: str = "1", *, graph_yaml: str = _BUILD_THEN_HUB_NODE) -> str:
    assert hub.client.post("/api/graphs", json={"definition_yaml": graph_yaml}).status_code == 201
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": ref})]})
    assert resp.status_code == 201, resp.text
    return resp.json()["chunk_id"]


def _held(hub: HubHarness, *, graph_yaml: str = _BUILD_THEN_HUB_NODE) -> tuple[str, str]:
    """A promoted, claimed chunk with its epoch-1 lease reported: its id and entry node id."""
    chunk_id = _mint(hub, graph_yaml=graph_yaml)
    hub.promote(chunk_id)
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": _RUNNER, "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    report_lease(hub, chunk_id, epoch=1, seq=1)
    return chunk_id, claim.json()["envelope"]["node"]["node_id"]


def _post(hub: HubHarness, path: str, body: dict[str, object] | None = None) -> None:
    resp = hub.client.post(path, json=body)
    assert resp.is_success, resp.text


def _complete(hub: HubHarness, chunk_id: str, node_id: str, choice: str) -> None:
    _post(
        hub,
        f"/api/fleet/chunks/{chunk_id}/completions",
        {"choice": choice, "epoch": 1, "runner_id": _RUNNER, "from_node_id": node_id, "artifacts": []},
    )


def _drive_promote(hub: HubHarness) -> None:
    hub.promote(_mint(hub))


def _drive_demote(hub: HubHarness) -> None:
    chunk_id = _mint(hub)
    hub.promote(chunk_id)
    _post(hub, f"/api/chunks/{chunk_id}/demote")


def _drive_group(hub: HubHarness) -> None:
    survivor, merged = _mint(hub, "1"), _mint(hub, "2")
    _post(hub, f"/api/chunks/{survivor}/group", {"merge_chunk_ids": [merged]})


def _drive_claim(hub: HubHarness) -> None:
    _held(hub)


def _drive_transition(hub: HubHarness) -> None:
    chunk_id, node_id = _held(hub)
    _complete(hub, chunk_id, node_id, "pass")


def _drive_hub_step(hub: HubHarness) -> None:
    chunk_id, node_id = _held(hub)
    _complete(hub, chunk_id, node_id, "pass")
    hub.settle_turns()


def _drive_migration(hub: HubHarness) -> None:
    assert hub.client.post("/api/graphs", json={"definition_yaml": _MIGRATION_TARGET}).status_code == 201
    chunk_id, node_id = _held(hub, graph_yaml=_MIGRATING)
    _complete(hub, chunk_id, node_id, "migrate")


def _drive_restart(hub: HubHarness) -> None:
    chunk_id, _node_id = _held(hub)
    _post(hub, f"/api/chunks/{chunk_id}/restart", {"node": "build"})


def _submit_decision(hub: HubHarness) -> str:
    chunk_id, node_id = _held(hub)
    _post(
        hub,
        f"/api/fleet/chunks/{chunk_id}/decisions",
        {
            "from_node_id": node_id,
            "epoch": 1,
            "runner_id": _RUNNER,
            "options": [{"name": "pass", "description": "Done."}],
            "artifacts": [],
        },
    )
    return hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"]["decision_id"]


def _drive_decision(hub: HubHarness) -> None:
    _submit_decision(hub)


def _drive_decision_resolution(hub: HubHarness) -> None:
    _post(hub, f"/api/decisions/{_submit_decision(hub)}/resolutions", {"choice": "pass"})


def _drive_question(hub: HubHarness) -> None:
    chunk_id, _node_id = _held(hub)
    report_question(hub, chunk_id, epoch=1, seq=2)


def _drive_answer(hub: HubHarness) -> None:
    _drive_question(hub)
    _post(hub, "/api/questions/q_1/answers", {"answer": "yes"})


def _drive_escalation(hub: HubHarness) -> None:
    chunk_id, _node_id = _held(hub)
    report_escalation(hub, chunk_id, epoch=1, seq=2)


def _drive_requeue(hub: HubHarness) -> None:
    chunk_id, _node_id = _held(hub)
    report_escalation(hub, chunk_id, epoch=1, seq=2)
    _post(hub, f"/api/chunks/{chunk_id}/requeues")


def _drive_detach(hub: HubHarness) -> None:
    chunk_id, _node_id = _held(hub)
    _post(hub, f"/api/chunks/{chunk_id}/detach")


def _drive_retirement(hub: HubHarness) -> None:
    _held(hub)
    _post(hub, f"/api/runners/{_RUNNER}/retire", {"by": "op", "force": True})


def _drive_release(hub: HubHarness) -> None:
    chunk_id, _node_id = _held(hub)
    _post(hub, f"/api/fleet/chunks/{chunk_id}/release", {"epoch": 1, "reason": "throttled"})


def _drive_pause(hub: HubHarness) -> None:
    chunk_id, _node_id = _held(hub)
    _post(hub, f"/api/chunks/{chunk_id}/pause", {"by": "alice"})
    _post(hub, f"/api/chunks/{chunk_id}/resume", {"by": "alice"})


def _drive_stop(hub: HubHarness) -> None:
    chunk_id, _node_id = _held(hub)
    _post(hub, f"/api/chunks/{chunk_id}/stop", {"by": "alice"})


def _drive_completion(hub: HubHarness) -> None:
    chunk_id, _node_id = _held(hub)
    _post(hub, f"/api/chunks/{chunk_id}/complete", {"by": "alice"})


def _drive_deletion(hub: HubHarness) -> None:
    chunk_id = _mint(hub)
    resp = hub.client.request("DELETE", f"/api/chunks/{chunk_id}", json={"by": "alice"})
    assert resp.is_success, resp.text


@dataclass(frozen=True)
class _Scenario:
    """One live path: the fact ``table`` it drives and, for a path that releases a held route, the cause the
    ``route_released`` row must record."""

    table: sa.Table
    drive: Callable[[HubHarness], None]
    releases: RouteReleaseCause | None = None


_SCENARIOS: dict[str, _Scenario] = {
    "promote": _Scenario(s.chunk_promoted, _drive_promote),
    "demote": _Scenario(s.chunk_demoted, _drive_demote),
    "group": _Scenario(s.chunk_grouped, _drive_group),
    "claim": _Scenario(s.route_created, _drive_claim),
    "transition": _Scenario(s.transitions, _drive_transition),
    "hub-step": _Scenario(s.transitions, _drive_hub_step, releases=RouteReleaseCause.HUB_STEP),
    "migration": _Scenario(s.chunk_migrations, _drive_migration, releases=RouteReleaseCause.MIGRATION),
    "restart": _Scenario(s.chunk_restarts, _drive_restart),
    "decision": _Scenario(s.decisions, _drive_decision),
    "decision-resolution": _Scenario(s.decision_resolutions, _drive_decision_resolution),
    "question": _Scenario(s.questions, _drive_question),
    "answer": _Scenario(s.question_answers, _drive_answer),
    "escalation": _Scenario(s.escalations, _drive_escalation),
    "requeue": _Scenario(s.requeues, _drive_requeue, releases=RouteReleaseCause.REQUEUE),
    "detach": _Scenario(s.route_released, _drive_detach, releases=RouteReleaseCause.DETACH),
    "retirement": _Scenario(s.route_released, _drive_retirement, releases=RouteReleaseCause.RETIREMENT),
    "release": _Scenario(s.route_released, _drive_release, releases=RouteReleaseCause.RELEASE),
    "pause": _Scenario(s.chunk_pause_facts, _drive_pause),
    "stop": _Scenario(s.chunk_stopped, _drive_stop, releases=RouteReleaseCause.STOP),
    "completion": _Scenario(s.chunk_completed, _drive_completion, releases=RouteReleaseCause.COMPLETE),
    "deletion": _Scenario(s.chunk_deleted, _drive_deletion),
}

#: Causes with no live path to drive: ``finalize_delivery`` has no production caller, delivery running as a hub step.
#: ``tests/component/hub/store/internal/test_activity_feed_store.py`` pins its cause and its absent row.
_NO_LIVE_PATH = frozenset({RouteReleaseCause.DELIVERY})


def _live_pairs(hub: HubHarness) -> set[tuple[str, str]]:
    frames = [json.loads(e["data"]) for e in emitted_events(hub) if e["event"] == HubEventType.CHUNK_CHANGED]
    keyed = [f for f in frames if f.get("key") is not None]
    # A deleted chunk's history leaves the feed, all but its deletion.
    deleted = {f["chunk_id"] for f in keyed if f["cause"] == "deleted"}
    return {(f["key"], f["cause"]) for f in keyed if f["chunk_id"] not in deleted or f["cause"] == "deleted"}


def _reconstructed_pairs(hub: HubHarness) -> set[tuple[str, str]]:
    resp = hub.client.get("/api/activity", params={"limit": 200})
    assert resp.status_code == 200, resp.text
    return {(r["key"], r["cause"]) for r in resp.json()["activity"] if r["type"] == "chunk-changed"}


def _release_causes(hub: HubHarness) -> list[str | None]:
    with hub.engine.connect() as conn:
        return list(conn.execute(hub_scoped(sa.select(s.route_released.c.cause))).scalars())


@pytest.mark.parametrize("name", _SCENARIOS)
def test_the_live_frames_and_the_reconstructed_feed_hold_the_same_rows(tmp_path: Path, name: str) -> None:
    scenario = _SCENARIOS[name]
    hub = build_hub(tmp_path)

    scenario.drive(hub)

    with hub.engine.connect() as conn:
        rows = conn.execute(hub_scoped(sa.select(sa.func.count()).select_from(scenario.table))).scalar_one()
    assert rows > 0, f"{name} wrote no {scenario.table.name} fact"
    live, reconstructed = _live_pairs(hub), _reconstructed_pairs(hub)
    assert reconstructed == live
    if scenario.releases is not None:
        assert _release_causes(hub) == [scenario.releases.value]


def test_every_fact_source_and_release_cause_has_a_live_path() -> None:
    assert {scenario.table for scenario in _SCENARIOS.values()} == set(EVENT_SOURCES)
    driven = {scenario.releases for scenario in _SCENARIOS.values() if scenario.releases is not None}
    assert driven == set(RouteReleaseCause) - _NO_LIVE_PATH
