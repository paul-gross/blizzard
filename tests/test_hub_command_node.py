"""The generic hub command node primitive (#65) — unit + component tiers.

Unit tier: graph validation plus the pure poll-policy and env-injection helpers. Component
tier: the executor taking actions' passes over a real hub store, including the
serialization barrier every parked chunk must honor.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict, Unpack, cast

import pytest
import yaml

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import Executor
from blizzard.hub.delivery.action_registry import HubActions
from blizzard.hub.delivery.actions.base import IHubAction
from blizzard.hub.domain.actions.effects import IEffectSink, Pass
from blizzard.hub.domain.actions.visit import GitCommits, UnconvergedDeliveryError, Visit
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import (
    ChunkFacts,
    HubNodePollFact,
    TransitionFact,
    WorkRef,
    decide_close_enqueue,
)
from blizzard.hub.domain.chunk.ports.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.delivery.flows import DEFAULT_POLL_INTERVAL, DEFAULT_POLL_TIMEOUT, PollPolicy
from blizzard.hub.domain.graph.actions import ActionSpec
from blizzard.hub.domain.graph.authoring import Reification
from blizzard.hub.domain.graph.model import GraphDoc
from blizzard.hub.domain.graph.validation import Validator
from blizzard.hub.forge.landing import VisitForges
from blizzard.hub.store import schema as s
from tests.support import (
    TEST_CATALOGUE,
    TEST_HUB_ACTIONS,
    FakeWorkSource,
    HubHarness,
    build_hub,
    chunk_stores,
    hub_store_connections,
    pointer_token,
    report_lease,
)

pytestmark = pytest.mark.unit

_POINTER = {"source": "default", "ref": "42"}

_HUB_CMD_GRAPH_YAML = """
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
          to: merge
        fail:
          description: Incomplete.
          to: build
  merge:
    executor: hub
    action: test.answer
    with: {choice: [success]}
    judgement:
      choices:
        success:
          description: Landed.
          to: done
        failure:
          description: Failed to land.
          to: build
"""


def _answering(choice: str) -> str:
    """:data:`_HUB_CMD_GRAPH_YAML` with ``merge`` answering ``choice``."""
    return _HUB_CMD_GRAPH_YAML.replace("with: {choice: [success]}", f"with: {{choice: [{choice}]}}")


# Unit — validation


def _errors(yaml_nodes: dict[str, object]) -> list[str]:
    doc = GraphDoc.of({"name": "g", "entry": "build", "nodes": yaml_nodes})
    return Validator.of(doc, TEST_CATALOGUE).result.errors


def test_hub_node_naming_an_action_and_choices_is_valid() -> None:
    doc = GraphDoc.of(
        {
            "name": "g",
            "entry": "build",
            "nodes": {
                "build": {
                    "executor": "runner",
                    "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "merge"}}},
                },
                "merge": {
                    "executor": "hub",
                    "action": "test.answer",
                    "with": {"choice": ["success"]},
                    "judgement": {
                        "choices": {
                            "success": {"description": "ok", "to": "done"},
                            "failure": {"description": "bad", "to": "build"},
                        }
                    },
                },
            },
        }
    )
    result = Validator.of(doc, TEST_CATALOGUE).result
    assert result.errors == []


def test_hub_node_naming_an_action_rejects_prompt() -> None:
    errors = _errors(
        {
            "build": {
                "executor": "runner",
                "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "merge"}}},
            },
            "merge": {
                "executor": "hub",
                "prompt": "not allowed",
                "action": "test.answer",
                "with": {"choice": ["success"]},
                "judgement": {"choices": {"success": {"description": "ok", "to": "done"}}},
            },
        }
    )
    assert any("must not declare `prompt`" in e for e in errors)


def test_hub_node_naming_an_action_rejects_checks() -> None:
    errors = _errors(
        {
            "build": {
                "executor": "runner",
                "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "merge"}}},
            },
            "merge": {
                "executor": "hub",
                "checks": ["mise run test"],
                "action": "test.answer",
                "with": {"choice": ["success"]},
                "judgement": {"choices": {"success": {"description": "ok", "to": "done"}}},
            },
        }
    )
    assert any("must not declare `checks`" in e for e in errors)


def test_hub_node_naming_an_action_rejects_judgement_prompt() -> None:
    errors = _errors(
        {
            "build": {
                "executor": "runner",
                "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "merge"}}},
            },
            "merge": {
                "executor": "hub",
                "action": "test.answer",
                "with": {"choice": ["success"]},
                "judgement": {"prompt": "assess it", "choices": {"success": {"description": "ok", "to": "done"}}},
            },
        }
    )
    assert any("must not declare `judgement.prompt`" in e for e in errors)


def test_hub_node_naming_an_action_requires_a_judgement() -> None:
    errors = _errors(
        {
            "build": {
                "executor": "runner",
                "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "merge"}}},
            },
            "merge": {"executor": "hub", "action": "test.answer", "with": {"choice": ["success"]}},
        }
    )
    assert any("must declare a judgement" in e for e in errors)


def test_a_bare_hub_node_with_no_action_and_no_judgement_is_rejected() -> None:
    """An ``executor: hub`` node must declare a judgement (its outcome choices)
    whether or not it also names an ``action:`` — no node name is privileged (#67)."""
    errors = _errors(
        {
            "build": {
                "executor": "runner",
                "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "deliver"}}},
            },
            "deliver": {"executor": "hub"},
        }
    )
    assert any("must declare a judgement" in e for e in errors)


# Unit — pending outcome (#66)


_BUILD_TO_MERGE = {
    "executor": "runner",
    "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "merge"}}},
}


class _MergeNodeFields(TypedDict, total=False):
    poll_interval: int
    poll_timeout: int


def _merge_with(**overrides: Unpack[_MergeNodeFields]) -> dict[str, object]:
    node: dict[str, object] = {
        "executor": "hub",
        "action": "test.sequence",
        "with": {"choices": ["pending", "success"]},
        "judgement": {
            "choices": {
                "success": {"description": "ok", "to": "done"},
                "failure": {"description": "bad", "to": "build"},
            }
        },
    }
    node.update(overrides)
    return node


def test_poll_interval_and_timeout_are_legal_only_on_a_hub_command_node() -> None:
    errors = _errors(
        {
            "build": {
                "executor": "runner",
                "poll_interval": 30,
                "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "done"}}},
            },
        }
    )
    assert any("only legal on a hub command node" in e for e in errors)


def test_poll_interval_must_be_positive() -> None:
    errors = _errors(
        {
            "build": _BUILD_TO_MERGE,
            "merge": _merge_with(poll_interval=0),
        }
    )
    assert any("`poll_interval` must be a positive number of seconds" in e for e in errors)


def test_poll_timeout_must_be_positive() -> None:
    errors = _errors(
        {
            "build": _BUILD_TO_MERGE,
            "merge": _merge_with(poll_timeout=-1),
        }
    )
    assert any("`poll_timeout` must be a positive number of seconds" in e for e in errors)


def test_poll_timeout_must_be_at_least_poll_interval() -> None:
    errors = _errors(
        {
            "build": _BUILD_TO_MERGE,
            "merge": _merge_with(poll_interval=60, poll_timeout=30),
        }
    )
    assert any("`poll_timeout` must be >= `poll_interval`" in e for e in errors)


def test_poll_interval_and_timeout_are_valid_when_well_formed() -> None:
    errors = _errors(
        {
            "build": _BUILD_TO_MERGE,
            "merge": _merge_with(poll_interval=30, poll_timeout=600),
        }
    )
    assert errors == []


def test_poll_policy_defaults_when_unauthored() -> None:
    _, merge_node = _reified_merge_node()
    assert PollPolicy.of(merge_node).interval == DEFAULT_POLL_INTERVAL
    assert PollPolicy.of(merge_node).timeout == DEFAULT_POLL_TIMEOUT


def test_poll_policy_honors_the_authored_override() -> None:
    doc = GraphDoc.of(
        {
            "name": "g",
            "entry": "merge",
            "nodes": {"merge": _merge_with(poll_interval=15, poll_timeout=90)},
        }
    )
    graph = Reification.of(doc, FixedClock(datetime(2026, 7, 17, tzinfo=UTC))).graph
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None
    assert PollPolicy.of(merge_node).interval == timedelta(seconds=15)
    assert PollPolicy.of(merge_node).timeout == timedelta(seconds=90)


def _facts_at_hub_node(
    *, node_id: str = "nd_merge", epoch: int = 1, polls: list[HubNodePollFact] | None = None
) -> ChunkFacts:
    return ChunkFacts(
        minted=True,
        transitions=[
            TransitionFact(
                to_node_id=node_id,
                to_node_executor=Executor.HUB,
                epoch=epoch,
                recorded_at=datetime(2026, 7, 17, tzinfo=UTC),
            )
        ],
        hub_node_polls=polls or [],
    )


def test_hub_node_pending_is_none_with_no_poll_fact() -> None:
    assert _facts_at_hub_node(polls=[]).hub_node_pending() is None


def test_pending_timeout_starts_at_the_latest_uninterrupted_pending_run() -> None:
    at = datetime(2026, 7, 17, tzinfo=UTC)
    polls = [
        HubNodePollFact("nd_merge", 1, at, kind="pending"),
        HubNodePollFact("nd_merge", 1, at + timedelta(hours=4), kind="awaiting-merge"),
        HubNodePollFact("nd_merge", 1, at + timedelta(hours=5), kind="pending"),
        HubNodePollFact("nd_merge", 1, at + timedelta(hours=6), kind="pending"),
    ]
    facts = _facts_at_hub_node(polls=polls)
    assert facts.hub_node_pending_since(node_id="nd_merge", epoch=1) == at + timedelta(hours=5)


def test_hub_node_pending_returns_the_newest_matching_poll_fact() -> None:
    facts = _facts_at_hub_node(
        polls=[
            HubNodePollFact(node_id="nd_merge", epoch=1, polled_at=datetime(2026, 7, 17, 0, 0, tzinfo=UTC)),
            HubNodePollFact(node_id="nd_merge", epoch=1, polled_at=datetime(2026, 7, 17, 0, 1, tzinfo=UTC)),
        ]
    )
    pending = facts.hub_node_pending()
    assert pending is not None
    assert pending.polled_at == datetime(2026, 7, 17, 0, 1, tzinfo=UTC)


def test_hub_node_pending_ignores_a_poll_fact_from_a_stale_epoch() -> None:
    """A poll fact recorded for an earlier visit to the SAME node (before a later
    transition re-entered it) must not read as pending now — epoch-keyed, not
    node-keyed alone."""
    stale_poll = HubNodePollFact(node_id="nd_merge", epoch=1, polled_at=datetime(2026, 7, 17, tzinfo=UTC))
    facts = _facts_at_hub_node(epoch=2, polls=[stale_poll])
    assert facts.hub_node_pending() is None


def test_hub_node_pending_is_none_off_a_non_hub_node() -> None:
    facts = ChunkFacts(
        minted=True,
        transitions=[
            TransitionFact(
                to_node_id="nd_build",
                to_node_executor=Executor.RUNNER,
                epoch=1,
                recorded_at=datetime(2026, 7, 17, tzinfo=UTC),
            )
        ],
        hub_node_polls=[HubNodePollFact(node_id="nd_build", epoch=1, polled_at=datetime(2026, 7, 17, tzinfo=UTC))],
    )
    assert facts.hub_node_pending() is None


# Unit — env injection


def _reified_merge_node():
    doc = GraphDoc.of({"name": "g", "entry": "build", "nodes": _yaml_nodes()})
    graph = Reification.of(doc, FixedClock(datetime(2026, 7, 17, tzinfo=UTC))).graph
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None
    return graph, merge_node


def _yaml_nodes() -> dict[str, object]:
    return yaml.safe_load(_HUB_CMD_GRAPH_YAML)["nodes"]


def _commit_row(
    *,
    node_name: str,
    epoch: int,
    commit: str,
    repo: str = "acme/widget",
    branch: str = "feat/x",
    forge: str | None = None,
) -> StoredArtifact:
    """One repo's declared git pointer, as the node at ``epoch`` recorded it.

    ``forge`` defaults to the origin the repo's own name implies, so a fixture naming two
    repos describes two distinct origins."""
    return StoredArtifact(
        kind=ArtifactKind.GIT_COMMIT,
        name=repo.rpartition("/")[2],  # named after the repo, never the literal produces name
        data=f"{branch}:{commit}",
        repo=repo,
        forge=forge if forge is not None else f"https://github.com/{repo}",
        artifact_id=f"art_{node_name}_{epoch}",
        chunk_id="ch_x",
        node_id=f"nd_{node_name}",
        node_name=node_name,
        epoch=epoch,
    )


def _delivered(artifacts: list[StoredArtifact]) -> list[dict[str, str | None]]:
    """The commits a visit over ``artifacts`` delivers, one per repository."""
    return GitCommits.of(artifacts).payload()


def test_a_rebased_tip_supersedes_the_pre_rebase_commit_for_the_same_repo() -> None:
    """The pre-push wedge (#130): a rebase REWRITES the branch, so `build`'s orphaned
    commit can never fast-forward. Delivery must see ONLY the rebased tip — keyed per
    ``(node_name, name)``, the later declaration wins."""
    commits = _delivered(
        [
            _commit_row(node_name="build", epoch=2, commit="a" * 40),
            _commit_row(node_name="pre-push", epoch=7, commit="b" * 40),
        ]
    )
    assert commits == [{"repo": "acme/widget", "branch": "feat/x", "commit": "b" * 40}]


def test_declaration_order_never_decides_which_commit_delivers() -> None:
    """Epoch decides, not row order — the store's ordering must not be load-bearing."""
    rows = [
        _commit_row(node_name="pre-push", epoch=7, commit="b" * 40),
        _commit_row(node_name="build", epoch=2, commit="a" * 40),
    ]
    assert _delivered(rows) == [{"repo": "acme/widget", "branch": "feat/x", "commit": "b" * 40}]


def test_a_multi_repo_chunk_keeps_one_commit_per_repo() -> None:
    """Collapsing is per repo, so a chunk spanning repos still delivers all of them."""
    commits = _delivered(
        [
            _commit_row(node_name="build", epoch=2, commit="a" * 40, repo="acme/widget"),
            _commit_row(node_name="build", epoch=2, commit="c" * 40, repo="acme/gadget"),
            _commit_row(node_name="pre-push", epoch=7, commit="b" * 40, repo="acme/widget"),
        ]
    )
    by_repo = {c["repo"]: c["commit"] for c in commits}
    assert by_repo == {"acme/widget": "b" * 40, "acme/gadget": "c" * 40}


def _to_merge_node(hub: HubHarness, pointer=_POINTER, graph_yaml: str = _HUB_CMD_GRAPH_YAML, tokens=None):
    """Ingest, promote, claim, and complete ``build`` -> ``merge`` for one chunk.

    Returns ``(chunk_id, build_node_id, graph)``. The completion's own apply already
    runs the hub node executor synchronously (``src/blizzard/hub/domain/execution/apply.py``'s hub-node branch) — most
    tests below want that; the barrier test bypasses it (:func:`_park_at_merge`)."""
    assert hub.client.post("/api/graphs", json={"definition_yaml": graph_yaml}).status_code == 201
    resp = hub.client.post("/api/chunks", json={"tokens": tokens or [pointer_token(pointer)]})
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert claim.status_code == 201, claim.text
    build_node_id = claim.json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    graph = hub.services.graphs.get(chunk.graph_id)
    assert graph is not None
    return chunk_id, build_node_id, graph


def _submit_build_pass(hub: HubHarness, chunk_id: str, build_node_id: str, epoch: int):
    """Complete ``build`` with ``pass``, then take every hub turn it leads to."""
    resp = post_build_pass(hub, chunk_id, build_node_id, epoch)
    hub.settle_turns()
    return resp


def post_build_pass(hub, chunk_id: str, build_node_id: str, epoch: int):
    return hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": epoch,
            "runner_id": "r1",
            "from_node_id": build_node_id,
            "check_results": [],
            "artifacts": [],
        },
    )


@pytest.mark.component
def test_a_restart_mid_hub_node_run_fences_out_a_stale_marker_write(tmp_path: Path) -> None:
    """A restart during a visit fences a stale merged marker and its landing
    side effect (``bzh:epoch-fencing``)."""
    hub = build_hub(tmp_path)
    chunk_id, _build_node_id, graph = _to_merge_node(hub)
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None
    before = hub.clock.now()

    assert hub.client.post(f"/api/chunks/{chunk_id}/restart", json={}).status_code == 202

    recorded = hub.services.hub_markers.record_marker(
        chunk_id,
        node_id=merge_node.node_id,
        node_name="merge",
        epoch=1,  # the run's own epoch, read before the restart moved the chunk on
        name="merged/acme-widget",
        content="sha:abc123",
    )

    assert recorded is False
    names = {a.name for a in hub.services.chunks.artifacts.load_artifacts(chunk_id)}
    assert "merged/acme-widget" not in names
    assert hub.services.chunks.delivery.count_landed_since("acme-widget", before) == 0


@pytest.mark.component
def test_a_blank_landing_marker_is_stored_but_records_no_landing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _build_node_id, graph = _to_merge_node(hub)
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None
    before = hub.clock.now() - timedelta(seconds=1)  # landed_at is read strictly after

    def intents() -> list[int]:
        return [i.intent_id for i in hub.services.chunks.delivery.pending_close_intents() if i.chunk_id == chunk_id]

    intents_before = intents()

    def record(name: str, content: str) -> bool:
        return hub.services.hub_markers.record_marker(
            chunk_id, node_id=merge_node.node_id, node_name="merge", epoch=1, name=name, content=content
        )

    assert record("merged/acme-blank", "  ") is True
    names = {a.name for a in hub.services.chunks.artifacts.load_artifacts(chunk_id)}
    assert "merged/acme-blank" in names
    assert hub.services.chunks.delivery.count_landed_since("acme-blank", before) == 0
    assert intents() == intents_before
    assert not [
        e for e in hub.services.chunks.events.list_events(chunk_id=chunk_id) if e.kind == "work-item-close-skipped"
    ]

    assert record("merged/acme-widget", "sha:abc123") is True
    assert hub.services.chunks.delivery.count_landed_since("acme-widget", before) == 1
    assert intents() == intents_before
    assert (
        len(
            [
                e
                for e in hub.services.chunks.events.list_events(chunk_id=chunk_id)
                if e.kind == "work-item-close-skipped"
            ]
        )
        == 1
    )


@pytest.mark.component
def test_a_stopped_chunk_fences_out_a_still_running_marker_write(tmp_path: Path) -> None:
    """Stop mints no epoch: terminality must fence the still-current run's marker
    (``bzh:epoch-fencing``), landing and close intent."""
    hub = build_hub(tmp_path)
    chunk_id, _build_node_id, graph = _to_merge_node(hub)
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None
    before = hub.clock.now()

    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"}).status_code == 202

    recorded = hub.services.hub_markers.record_marker(
        chunk_id,
        node_id=merge_node.node_id,
        node_name="merge",
        epoch=1,  # the run's own epoch — a stop mints no fresher one to fence it out
        name="merged/acme-widget",
        content="sha:abc123",
    )

    assert recorded is False
    names = {a.name for a in hub.services.chunks.artifacts.load_artifacts(chunk_id)}
    assert "merged/acme-widget" not in names
    assert hub.services.chunks.delivery.count_landed_since("acme-widget", before) == 0
    assert [i for i in hub.services.chunks.delivery.pending_close_intents() if i.chunk_id == chunk_id] == []


@pytest.mark.component
def test_a_stopped_chunk_fences_out_a_still_running_step_transition(tmp_path: Path) -> None:
    """``record_hub_step_transition`` mirrors ``record_hub_artifact``'s guards (#65) — the
    sibling of the marker-write case above: a stop landing mid-visit must fence the
    node's own exit transition too, not just its marker."""
    hub = build_hub(tmp_path)
    chunk_id, _build_node_id, graph = _to_merge_node(hub)
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None

    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"}).status_code == 202

    stores = chunk_stores(hub.engine, hub.clock)
    with stores.exclusive.locked([chunk_id]) as handle:
        recorded = stores.hub_exec.record_hub_step_transition_locked(
            handle,
            chunk_id,
            from_node_id=merge_node.node_id,
            to_node_id="done",
            choice_name="success",
            epoch=1,  # the run's own epoch — a stop mints no fresher one to fence it out
            runner_id="r1",
            transition_id="tr_stale",
            close_decision=decide_close_enqueue([WorkRef(**_POINTER)], lambda _: True),
            at=hub.clock.now(),
            artifacts=[],
            release_route=True,
            admission=EpochAdmission.ABOVE,
        )

    assert recorded is False
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "stopped"
    assert all(t["from_node_name"] != "merge" for t in detail["history"])


class _Gate:
    """An in-process action holding each pass until released, counting the passes it took and how many ran at
    once."""

    spec = ActionSpec(
        name="test.gate",
        args=frozenset(),
        required_args=frozenset(),
        answers=frozenset({"success"}),
        answers_from_arg=None,
        lands=False,
    )

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()
        self.passes = 0
        self.in_flight = 0
        self.max_in_flight = 0

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        with self._lock:
            self.passes += 1
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.entered.set()
        self.release.wait(timeout=5)
        with self._lock:
            self.in_flight -= 1
        return Pass(choice="success")


class _Recorded:
    """An in-process action answering ``pending`` on its first pass and ``success`` after, keeping every visit
    it was handed."""

    spec = ActionSpec(
        name="test.recorded",
        args=frozenset(),
        required_args=frozenset(),
        answers=frozenset({"pending", "success"}),
        answers_from_arg=None,
        lands=False,
    )

    def __init__(self) -> None:
        self.visits: list[Visit] = []

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        self.visits.append(visit)
        return Pass(choice="pending" if len(self.visits) == 1 else "success")


def _hub_with(
    tmp_path: Path,
    *extra: IHubAction,
    work_sources: dict[str, FakeWorkSource] | None = None,
    public_url: str | None = None,
) -> HubHarness:
    actions = HubActions.of([*TEST_HUB_ACTIONS.implementations.values(), *extra])
    return build_hub(tmp_path, actions=actions, work_sources=work_sources, public_url=public_url)


def _gated(choice_action: str = "test.gate") -> str:
    return _HUB_CMD_GRAPH_YAML.replace("action: test.answer\n    with: {choice: [success]}", f"action: {choice_action}")


def _passes(hub: HubHarness, chunk_id: str) -> int:
    """How many ``test.sequence`` passes the chunk's node took — one marker each."""
    names = [a.name for a in hub.services.chunks.artifacts.load_artifacts(chunk_id)]
    return sum(1 for name in names if name.startswith("test-sequence/"))


@pytest.mark.component
def test_a_pass_maps_its_answer_to_the_authored_edge(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, _graph = _to_merge_node(hub)

    apply = _submit_build_pass(hub, chunk_id, build_node_id, 1)
    assert apply.json()["outcome"] == "hub_node_taken"

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "done"
    land_transitions = [t for t in detail["history"] if t["from_node_name"] == "merge"]
    assert len(land_transitions) == 1
    assert land_transitions[0]["choice_name"] == "success"
    assert land_transitions[0]["to_node_id"] == "done"  # the reserved terminal — no node to name


@pytest.mark.component
def test_a_hub_node_landing_done_releases_the_route(tmp_path: Path) -> None:
    """The hub node's ``done`` is the release a runner-authored ``done`` never writes."""
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, _graph = _to_merge_node(hub)

    assert _submit_build_pass(hub, chunk_id, build_node_id, 1).json()["outcome"] == "hub_node_taken"

    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"
    assert hub.services.chunks.route.route_of(chunk_id) is None


@pytest.mark.component
def test_a_failed_work_item_fetch_degrades_the_feature_title_to_absent(tmp_path: Path) -> None:
    """A forge read failing while resolving the feature title must never break delivery
    : the turn still takes its pass, its visit naming no title."""
    recorded = _Recorded()
    hub = _hub_with(tmp_path, recorded, work_sources={"default": FakeWorkSource(fail_refs={"42"})})
    chunk_id, build_node_id, _graph = _to_merge_node(hub, graph_yaml=_gated("test.recorded"))

    assert _submit_build_pass(hub, chunk_id, build_node_id, 1).json()["outcome"] == "hub_node_taken"

    assert [visit.feature_title for visit in recorded.visits] == [None]


@pytest.mark.component
def test_a_failure_answer_maps_to_the_failure_edge(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, _graph = _to_merge_node(hub, graph_yaml=_answering("failure"))

    apply = _submit_build_pass(hub, chunk_id, build_node_id, 1)
    assert apply.json()["outcome"] == "hub_node_taken"

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    # Routed back to `build` (the `failure` edge's target) — running again, not done.
    assert detail["status"] == "running"
    assert detail["current_node_name"] == "build"


def _submit_build_pass_with_commit(hub: HubHarness, chunk_id: str, build_node_id: str, epoch: int, *, repo: str):
    """Like :func:`_submit_build_pass`, but carrying one ``git_commit`` artifact for
    ``repo`` — the shape a hub node's kick-back accounting reads to tell a genuine
    delivery attempt from a bare hub-command smoke test."""
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": epoch,
            "runner_id": "r1",
            "from_node_id": build_node_id,
            "check_results": [],
            "artifacts": [
                {
                    "name": "w",
                    "kind": "git_commit",
                    "repo": repo,
                    "branch_name": "b",
                    "commit_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                }
            ],
        },
    )
    hub.settle_turns()
    return resp


@pytest.mark.component
def test_a_non_terminal_route_with_nothing_landed_records_a_bounce(tmp_path: Path) -> None:
    """A non-terminal route with nothing landed is a delivery kick-back (#64) — by
    the domain fact (no ``merged/<repo>`` marker), never by choice name."""
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, _graph = _to_merge_node(hub, graph_yaml=_answering("failure"))

    apply = _submit_build_pass_with_commit(hub, chunk_id, build_node_id, 1, repo="acme/widget")
    assert apply.json()["outcome"] == "hub_node_taken"

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["current_node_name"] == "build"
    assert len(detail["bounces"]) == 1
    assert detail["bounces"][0]["cause"] == "failure"
    bounce_assets = [a for a in detail["artifacts"] if a["name"] == "bounce-envelope"]
    assert len(bounce_assets) == 1, detail["artifacts"]
    assert detail["landed"] is False


@pytest.mark.component
def test_a_fully_landed_non_terminal_route_records_no_bounce(tmp_path: Path) -> None:
    """The counterpart: every repo already carries its ``merged/<repo>`` marker, so a
    non-terminal continuation (an authored ``landed -> <node>`` edge, #63) is forward
    progress, never a kick-back — no bounce fact, whatever the outcome's name."""
    hub = build_hub(tmp_path)
    graph_yaml = """
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
          to: merge
        fail:
          description: Incomplete.
          to: build
  merge:
    executor: hub
    action: test.answer
    with: {choice: [landed]}
    judgement:
      choices:
        landed:
          description: Landed; continue to verify.
          to: verify
        failure:
          description: Failed to land.
          to: build
  verify:
    executor: runner
    prompt: |
      Confirm.
    judgement:
      prompt: |
        Assess the landed change.
      choices:
        pass:
          description: Confirmed.
          to: done
"""
    chunk_id, build_node_id, graph = _to_merge_node(hub, graph_yaml=graph_yaml)
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None
    # The repo already landed — pre-record the marker as an earlier pass would have.
    cast(IWriteChunkArtifactsRepository, hub.services.chunks.artifacts).record_hub_artifact(
        chunk_id,
        node_id=merge_node.node_id,
        node_name="merge",
        epoch=1,
        name="merged/acme/widget",
        close_decision=decide_close_enqueue([WorkRef(**_POINTER)], lambda _: True),
        content="c",
        at=hub.clock.now(),
        admission=EpochAdmission.AT_OR_ABOVE,
    )

    apply = _submit_build_pass_with_commit(hub, chunk_id, build_node_id, 1, repo="acme/widget")
    assert apply.json()["outcome"] == "hub_node_taken"

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["current_node_name"] == "verify"
    assert detail["bounces"] == []
    assert detail["landed"] is True


@pytest.mark.component
def test_mid_run_marker_callback_records_a_marker(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _HUB_CMD_GRAPH_YAML}).status_code == 201
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]})
    chunk_id = resp.json()["chunk_id"]
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    graph = hub.services.graphs.get(chunk.graph_id)
    assert graph is not None
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None

    marker = hub.client.post(
        f"/api/chunks/{chunk_id}/hub-markers?node_id={merge_node.node_id}&epoch=1",
        json={"name": "merged/acme-widget", "content": "sha:abc123"},
    )
    assert marker.status_code == 200, marker.text
    assert marker.json()["recorded"] is True

    # Idempotent per (chunk, node, name, epoch) — a second post is a harmless no-op.
    replay = hub.client.post(
        f"/api/chunks/{chunk_id}/hub-markers?node_id={merge_node.node_id}&epoch=1",
        json={"name": "merged/acme-widget", "content": "sha:abc123"},
    )
    assert replay.json()["recorded"] is False

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert any(a["name"] == "merged/acme-widget" for a in detail["artifacts"])


@pytest.mark.component
def test_a_fresh_merged_marker_records_the_delivery_landed_fact(tmp_path: Path) -> None:
    """A `merged/<repo>` marker is the sole production choke point for
    `count_landed_since` — a fresh write records it, a replay never double-counts, and a
    non-`merged/` marker never touches it at all."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _HUB_CMD_GRAPH_YAML}).status_code == 201
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]})
    chunk_id = resp.json()["chunk_id"]
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    graph = hub.services.graphs.get(chunk.graph_id)
    assert graph is not None
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None

    since = hub.clock.now() - timedelta(seconds=1)

    other_marker = hub.client.post(
        f"/api/chunks/{chunk_id}/hub-markers?node_id={merge_node.node_id}&epoch=1",
        json={"name": "hub-log.land", "content": "irrelevant"},
    )
    assert other_marker.status_code == 200, other_marker.text
    assert hub.services.chunks.delivery.count_landed_since("acme-widget", since) == 0

    marker = hub.client.post(
        f"/api/chunks/{chunk_id}/hub-markers?node_id={merge_node.node_id}&epoch=1",
        json={"name": "merged/acme-widget", "content": "sha:abc123"},
    )
    assert marker.status_code == 200, marker.text
    assert marker.json()["recorded"] is True
    assert hub.services.chunks.delivery.count_landed_since("acme-widget", since) == 1
    with hub.engine.begin() as conn:
        row = conn.execute(
            hub_store_connections(hub.engine).select(
                s.delivery_repo_landed,
                s.delivery_repo_landed.c.repo,
                s.delivery_repo_landed.c.commit_hash,
                s.delivery_repo_landed.c.landed_at,
            )
        ).one()
        assert row.repo == "acme-widget"
        assert row.commit_hash == "sha:abc123"
        assert row.landed_at == hub.clock.now()

    # Idempotent per (chunk, node, name, epoch) — a replay's `record_hub_artifact` returns
    # False, so the delivery-landed count must not move (crash-recovery double-count).
    replay = hub.client.post(
        f"/api/chunks/{chunk_id}/hub-markers?node_id={merge_node.node_id}&epoch=1",
        json={"name": "merged/acme-widget", "content": "sha:abc123"},
    )
    assert replay.json()["recorded"] is False
    assert hub.services.chunks.delivery.count_landed_since("acme-widget", since) == 1


@pytest.mark.component
def test_hub_advance_endpoint_no_ops_off_a_non_hub_command_node(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _HUB_CMD_GRAPH_YAML}).status_code == 201
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]})
    chunk_id = resp.json()["chunk_id"]
    result = hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    assert result.status_code == 200
    assert result.json()["ran"] is False


@pytest.mark.component
def test_a_chunks_own_live_turn_is_never_reentered_and_a_finished_one_never_replays(tmp_path: Path) -> None:
    """While a chunk's hub node takes its turn, another tick takes none for that chunk; once the turn ended, a
    replayed completion and further ticks take none either."""
    gate = _Gate()
    hub = _hub_with(tmp_path, gate)
    chunk_id, build_node_id, _graph = _to_merge_node(hub, graph_yaml=_gated())
    assert post_build_pass(hub, chunk_id, build_node_id, 1).json()["outcome"] == "hub_node_taken"

    thread = threading.Thread(target=hub.take_turns)
    thread.start()
    assert gate.entered.wait(timeout=5), "the hub node never started"

    (during,) = hub.take_turns()
    assert [t for t in during.started if t.submission.chunk_id == chunk_id] == []
    assert gate.passes == 1

    gate.release.set()
    thread.join(timeout=5)

    assert _submit_build_pass(hub, chunk_id, build_node_id, 1).json()["outcome"] == "hub_node_taken"
    assert gate.passes == 1
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert len([t for t in detail["history"] if t["from_node_name"] == "merge"]) == 1


# Component — pending outcome (#66): polls without blocking the queue

_POLL_GRAPH_YAML = """
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
          to: merge
        fail:
          description: Incomplete.
          to: build
  merge:
    executor: hub
    poll_interval: 30
    poll_timeout: 90
    action: test.sequence
    with: {choices: [pending, success]}
    judgement:
      choices:
        success:
          description: Green.
          to: done
        failure:
          description: Red.
          to: build
"""

#: ``merge`` answering ``pending`` on every pass.
_ALWAYS_PENDING_GRAPH_YAML = _POLL_GRAPH_YAML.replace(
    "with: {choices: [pending, success]}", "with: {choices: [pending]}"
)


def _to_poll_merge_node(hub: HubHarness, pointer, graph_yaml: str = _POLL_GRAPH_YAML):
    """Same shape as ``_to_merge_node`` but minting a pending-capable poll graph."""
    return _to_merge_node(hub, pointer=pointer, graph_yaml=graph_yaml)


@pytest.mark.component
def test_pending_records_no_transition_and_ends_the_turn(tmp_path: Path) -> None:
    """A ``pending`` answer parks the chunk without a transition, and the fleet-wide
    turn ends with it — the property a pending node must hold."""
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, _graph = _to_poll_merge_node(hub, {"source": "default", "ref": "poll-1"})

    apply = _submit_build_pass(hub, chunk_id, build_node_id, 1)
    assert apply.json()["outcome"] == "hub_node_taken"

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "delivering"
    assert detail["current_node_name"] == "merge"
    assert detail["pending"] is not None
    assert detail["pending"]["node_name"] == "merge"
    # No bounce recorded — pending itself is not contention (#64's kick-back path).
    assert detail["bounces"] == []


@pytest.mark.component
def test_hub_advance_respects_the_poll_interval_then_succeeds(tmp_path: Path) -> None:
    """Before ``poll_interval`` elapses the driver takes no turn; once elapsed, the node takes its next pass and
    a ``success`` routes ``done``."""
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, _graph = _to_poll_merge_node(hub, {"source": "default", "ref": "poll-2"})

    apply = _submit_build_pass(hub, chunk_id, build_node_id, 1)
    assert apply.json()["outcome"] == "hub_node_taken"
    assert _passes(hub, chunk_id) == 1

    # Not yet due (poll_interval: 30) — the driver takes no turn.
    assert hub.turn_outcome(chunk_id) is None
    assert _passes(hub, chunk_id) == 1

    hub.clock.advance(timedelta(seconds=31))
    assert hub.turn_outcome(chunk_id) == "success"
    assert _passes(hub, chunk_id) == 2

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "done"
    assert detail["pending"] is None


@pytest.mark.component
def test_a_slug_renamed_between_two_passes_of_one_chunk_reaches_the_second_under_the_same_id(
    tmp_path: Path,
) -> None:
    """Each pass reads its chunk's project as it starts: a re-poll after a rename names the new slug."""
    recorded = _Recorded()
    hub = _hub_with(tmp_path, recorded)
    project_id = hub.client.get("/api/projects/default").json()["project_id"]
    graph_yaml = _POLL_GRAPH_YAML.replace(
        "action: test.sequence\n    with: {choices: [pending, success]}", "action: test.recorded"
    )
    chunk_id, build_node_id, _graph = _to_poll_merge_node(
        hub, {"source": "default", "ref": "poll-rename"}, graph_yaml=graph_yaml
    )
    assert _submit_build_pass(hub, chunk_id, build_node_id, 1).json()["outcome"] == "hub_node_taken"

    renamed = hub.client.patch("/api/projects/default", json={"slug": "widgets"})
    assert renamed.status_code == 200, renamed.text
    hub.clock.advance(timedelta(seconds=31))
    assert hub.turn_outcome(chunk_id) == "success"

    assert [(visit.project.project_id, visit.project.slug) for visit in recorded.visits] == [
        (project_id, "default"),
        (project_id, "widgets"),
    ]


@pytest.mark.component
def test_pending_chunk_does_not_block_another_chunks_hub_node(tmp_path: Path) -> None:
    """The key #66 property: chunk A parked pending (slot released) must not prevent
    chunk B's hub node from running to completion on its own pass."""
    hub = build_hub(tmp_path)
    chunk_a, build_a, _graph_a = _to_poll_merge_node(
        hub, {"source": "default", "ref": "poll-a"}, graph_yaml=_ALWAYS_PENDING_GRAPH_YAML
    )
    assert _submit_build_pass(hub, chunk_a, build_a, 1).json()["outcome"] == "hub_node_taken"
    assert hub.client.get(f"/api/chunks/{chunk_a}").json()["pending"] is not None  # parked, slot released

    # B's graph, answering `success`, is minted after A's, so a chunk ingested now pins it.
    hub.clock.advance(timedelta(seconds=1))
    chunk_b, build_b, _graph_b = _to_merge_node(hub, pointer={"source": "default", "ref": "poll-b"})
    assert _submit_build_pass(hub, chunk_b, build_b, 1).json()["outcome"] == "hub_node_taken"

    assert hub.client.get(f"/api/chunks/{chunk_b}").json()["status"] == "done"
    # A is still parked pending, unaffected.
    assert hub.client.get(f"/api/chunks/{chunk_a}").json()["pending"] is not None


@pytest.mark.component
def test_poll_timeout_routes_the_failure_edge_via_the_kickback_path(tmp_path: Path) -> None:
    """Pending past ``poll_timeout`` stops polling and kicks back via #64's bounce
    accounting — a bounce fact recorded, routed to the ``failure`` edge's target
    (``build``), below the fleet-wide default ``bounce_cap`` so no escalation yet."""
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, _graph = _to_poll_merge_node(
        hub, {"source": "default", "ref": "poll-timeout"}, graph_yaml=_ALWAYS_PENDING_GRAPH_YAML
    )

    apply = _submit_build_pass(hub, chunk_id, build_node_id, 1)
    assert apply.json()["outcome"] == "hub_node_taken"  # poll #1 at t0

    hub.clock.advance(timedelta(seconds=31))
    assert hub.turn_outcome(chunk_id) == "pending"  # poll #2 at t0+31
    hub.clock.advance(timedelta(seconds=31))
    assert hub.turn_outcome(chunk_id) == "pending"  # poll #3 at t0+62
    assert _passes(hub, chunk_id) == 3

    # poll_timeout: 90, measured from the FIRST poll (t0) — elapsed is now 62 + 31 = 93 >= 90.
    hub.clock.advance(timedelta(seconds=31))
    assert hub.turn_outcome(chunk_id) == "failure"
    # No 4th pass is taken — the timeout check pre-empts it.
    assert _passes(hub, chunk_id) == 3

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "running"
    assert detail["current_node_name"] == "build"
    assert detail["pending"] is None
    assert len(detail["bounces"]) == 1
    assert detail["bounces"][0]["cause"] == "poll-timeout"
    envelope = json.loads(detail["bounces"][0]["envelope"])
    assert "poll_timeout" in envelope["detail"]
    assert envelope["unlanded"] == []
    assert envelope["step"] is None
    assert envelope["action"] is None


@pytest.mark.component
@pytest.mark.parametrize("landed", [False, True], ids=["unlanded", "landed"])
def test_a_poll_timeout_envelope_lists_a_seeded_commit_until_its_repo_lands(tmp_path: Path, landed: bool) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, graph = _to_poll_merge_node(
        hub, {"source": "default", "ref": "poll-commit"}, graph_yaml=_ALWAYS_PENDING_GRAPH_YAML
    )
    _submit_build_pass_with_commit(hub, chunk_id, build_node_id, 1, repo="acme/widget")
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None
    if landed:
        assert hub.services.hub_markers.record_marker(
            chunk_id, node_id=merge_node.node_id, node_name="merge", epoch=1, name="merged/acme/widget", content="sha"
        )

    hub.clock.advance(timedelta(seconds=91))
    assert hub.turn_outcome(chunk_id) == "failure"

    (bounce,) = hub.client.get(f"/api/chunks/{chunk_id}").json()["bounces"]
    unlanded = [u["repo"] for u in json.loads(bounce["envelope"])["unlanded"]]
    assert unlanded == ([] if landed else ["acme/widget"])


@pytest.mark.component
def test_poll_timeout_escalates_once_the_bounce_cap_is_crossed(tmp_path: Path) -> None:
    """Repeated poll-timeouts are bounces (#64): crossing ``bounce_cap`` escalates
    ``needs_human`` instead of routing back, exactly as a repeated conflict would."""
    graph_yaml = _ALWAYS_PENDING_GRAPH_YAML.replace("poll_timeout: 90", "poll_timeout: 30\n    bounce_cap: 1")
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, _graph = _to_poll_merge_node(
        hub, {"source": "default", "ref": "poll-cap"}, graph_yaml=graph_yaml
    )

    # Bounce #1 (poll_timeout: 30, one poll at t0 then straight past the bound).
    _submit_build_pass(hub, chunk_id, build_node_id, 1)
    hub.clock.advance(timedelta(seconds=31))
    assert hub.turn_outcome(chunk_id) == "failure"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "running"  # below the cap (1) — routed back, not escalated
    assert len(detail["bounces"]) == 1

    # Bounce #2 escalates; advance the clock for the transition tie-break.
    # Build mints above the hub-owned exit epoch (2).
    hub.clock.advance(timedelta(seconds=1))
    report_lease(hub, chunk_id, epoch=3, seq=2)
    second_build_node = hub.client.get(f"/api/chunks/{chunk_id}").json()["current_node_id"]
    _submit_build_pass(hub, chunk_id, second_build_node, 3)
    hub.clock.advance(timedelta(seconds=31))
    assert hub.turn_outcome(chunk_id) == "escalated"
    detail2 = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail2["status"] == "needs_human"
    assert len(detail2["bounces"]) == 2
    # A hub-authored escalation composes no wrapped command — the hub has no runner
    # runtime dir to draw one from (`blizzard-context:/domain/humans/escalation.md` §What each origin carries).
    assert detail2["escalation"]["takeover_command"] == ""
    assert detail2["escalation"]["wrapped_takeover_command"] == ""
    assert detail2["escalation"]["cause"] == "bounce-cap"
    assert detail2["escalation"]["detail"] == "bounce cap (1) crossed after 2 bounces"


def test_a_later_epoch_supersedes_an_earlier_pointer_for_the_same_repo() -> None:
    """The rebase case the per-repo key exists for: pre-push rewrites build's branch, so
    the later declaration replaces it rather than delivery being handed both."""
    commits = _delivered(
        [
            _commit_row(node_name="build", epoch=1, commit="a" * 40),
            _commit_row(node_name="pre-push", epoch=3, commit="b" * 40),
        ]
    )

    assert commits == [{"repo": "acme/widget", "branch": "feat/x", "commit": "b" * 40}]


def test_an_identical_re_declaration_at_one_epoch_is_a_correction_not_a_conflict() -> None:
    commits = _delivered(
        [
            _commit_row(node_name="build", epoch=2, commit="a" * 40),
            _commit_row(node_name="build", epoch=2, commit="a" * 40),
        ]
    )

    assert commits == [{"repo": "acme/widget", "branch": "feat/x", "commit": "a" * 40}]


def test_two_branches_for_one_repo_at_one_epoch_refuse_to_deliver() -> None:
    """A chunk declaring a branch per env at the same epoch is unconverged: keeping
    whichever came last would deliver a fraction of the work and call it a landing, so
    it is refused, not tie-broken. Convergence belongs to pre-push."""
    with pytest.raises(UnconvergedDeliveryError) as exc:
        _delivered(
            [
                _commit_row(node_name="build", epoch=2, commit="a" * 40, branch="feat/from-e1"),
                _commit_row(node_name="build", epoch=2, commit="b" * 40, branch="feat/from-e2"),
            ]
        )

    assert "acme/widget" in str(exc.value)
    assert "rolled up" in str(exc.value)


def test_delivery_addresses_a_repo_by_the_owner_its_own_origin_names() -> None:
    """Two repos under two owners in one chunk: each is addressed by the coordinate its
    own origin encodes, rather than both being re-qualified with one workspace-wide
    owner — which could only ever be right for one of them."""
    commits = _delivered(
        [
            _commit_row(node_name="build", epoch=1, commit="a" * 40, repo="acme/widget"),
            _commit_row(node_name="build", epoch=1, commit="b" * 40, repo="other-org/gadget"),
        ]
    )

    assert {c["repo"] for c in commits} == {"acme/widget", "other-org/gadget"}


def test_delivery_falls_back_to_the_bare_name_for_an_origin_that_names_no_owner() -> None:
    """The verification forge fronts flat bare origins that resolve under any owner, so
    an unqualifiable origin must leave the bare name rather than inventing a
    coordinate."""
    commits = _delivered(
        [
            _commit_row(
                node_name="build",
                epoch=1,
                commit="a" * 40,
                repo="toy-api",
                forge="file:///origins/toy-api.git",
            )
        ]
    )

    assert commits == [{"repo": "toy-api", "branch": "feat/x", "commit": "a" * 40}]


def test_the_same_name_under_two_owners_stays_two_repositories() -> None:
    """Production rows carry the bare manifest name in ``repo`` and the origin in ``forge``;
    identity is read from the origin, so neither owner's pointer merges into or supersedes
    the other's — at one epoch or across epochs."""
    at_one_epoch = _delivered(
        [
            _commit_row(
                node_name="build", epoch=1, commit="a" * 40, repo="widget", forge="https://github.com/owner-a/widget"
            ),
            _commit_row(
                node_name="build", epoch=1, commit="b" * 40, repo="widget", forge="https://github.com/owner-b/widget"
            ),
        ]
    )
    across_epochs = _delivered(
        [
            _commit_row(
                node_name="build", epoch=1, commit="a" * 40, repo="widget", forge="https://github.com/owner-a/widget"
            ),
            _commit_row(
                node_name="build", epoch=2, commit="b" * 40, repo="widget", forge="https://github.com/owner-b/widget"
            ),
        ]
    )

    assert {c["repo"] for c in at_one_epoch} == {"owner-a/widget", "owner-b/widget"}
    assert {c["repo"] for c in across_epochs} == {"owner-a/widget", "owner-b/widget"}


def test_one_identity_declared_twice_with_different_commits_refuses_to_deliver() -> None:
    with pytest.raises(UnconvergedDeliveryError) as exc:
        _delivered(
            [
                _commit_row(
                    node_name="build", epoch=1, commit="a" * 40, repo="widget", forge="git@github.com:acme/widget.git"
                ),
                _commit_row(
                    node_name="build", epoch=1, commit="b" * 40, repo="widget", forge="https://github.com/acme/widget"
                ),
            ]
        )

    assert "acme/widget" in str(exc.value)


def test_declares_git_commit_reads_the_graphs_own_intent() -> None:
    """The signal that tells a lost commit apart from one never promised. Read off the
    graph rather than the artifacts, so a code graph that produced nothing still counts
    as expecting something — which is the whole case being caught."""
    graph, _ = _reified_merge_node()

    declares = graph.declares_git_commit

    assert declares is any(spec.kind is ArtifactKind.GIT_COMMIT for node in graph.nodes for spec in node.produces)


@pytest.mark.component
def test_the_visit_names_every_work_item_by_its_forge_reference_and_links_the_board(tmp_path: Path) -> None:
    """A chunk mixing a forge-sourced item and a built-in ``hub`` item hands the pass one
    ``{label, reference}`` per item, in ``work_refs`` order, resolved with no forge read — the
    ``hub`` item has no forge reference to give."""
    recorded = _Recorded()
    source = FakeWorkSource(repo="acme/widget")
    hub = _hub_with(tmp_path, recorded, work_sources={"default": source}, public_url="https://blizzard.example.com")
    graph_yaml = _gated("test.recorded")
    assert hub.client.post("/api/graphs", json={"definition_yaml": graph_yaml}).status_code == 201
    minted = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()
    hub_ref = minted["ref"]
    # Minting seats the item on its own chunk; stopping that chunk frees the ref to ride along.
    assert hub.client.post(f"/api/chunks/{minted['chunk_id']}/stop", json={"by": "operator"}).status_code == 202
    chunk_id, build_node_id, _graph = _to_merge_node(
        hub, graph_yaml=graph_yaml, tokens=["default:42", f"hub:{hub_ref}"]
    )
    fetched_before = list(source.fetched)

    assert _submit_build_pass(hub, chunk_id, build_node_id, 1).json()["outcome"] == "hub_node_taken"

    (visit,) = recorded.visits
    assert [dict(item) for item in visit.work_items] == [
        {"label": "default#42", "reference": "acme/widget#42"},
        {"label": f"hub:{hub_ref}", "reference": None},
    ]
    assert visit.public_url == "https://blizzard.example.com"
    # Only the feature title's one read of the first item — the references read nothing.
    assert source.fetched == [*fetched_before, "42"]
