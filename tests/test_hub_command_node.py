"""The generic hub command node primitive (#65) — unit + component tiers.

Unit tier: graph parsing/validation for ``executor: hub`` + ``run:``, and the pure
outcome-mapping / env-injection helpers in
:mod:`blizzard.hub.delivery.hub_node`. Component tier: the executor wired with a
FAKE :class:`~blizzard.hub.delivery.command_runner.IHubCommandRunner` and
:class:`~blizzard.hub.delivery.workdir.IHubWorkdir` over a real hub store — the
``produces:`` skip, the mid-run marker callback, the stdout/stderr asset, the full
outcome->edge routing, and the **serialization barrier** (flagged, load-bearing):
two chunks parked at a hub command node must never run commands concurrently.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
import yaml

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.crash import discover_crash_points
from blizzard.hub.delivery.command_runner import CommandResult
from blizzard.hub.delivery.hub_node import (
    DEFAULT_POLL_INTERVAL,
    DEFAULT_POLL_TIMEOUT,
    HubEnvInputs,
    _printed_choice,
    build_hub_env,
    poll_interval_for,
    poll_timeout_for,
)
from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactRow
from blizzard.hub.domain.graph import HUB_PENDING_CHOICE, Executor, parse_graph_doc
from blizzard.hub.domain.graph_authoring import reify_graph
from blizzard.hub.domain.graph_validation import validate_graph
from blizzard.hub.domain.work import (
    Chunk,
    ChunkFacts,
    HubNodePollFact,
    IWriteChunkRepository,
    TransitionFact,
    hub_node_pending,
)
from tests.support import (
    FakeHubCommandRunner,
    FakeHubWorkdir,
    FakePmSource,
    HubHarness,
    build_hub,
    pointer_token,
    report_lease,
)


def _writable(hub: HubHarness) -> IWriteChunkRepository:
    """A test-only cast: ``HubHarness.services.chunks`` is read-typed
    (``bzh:controller-read-only`` — a controller depends on the read variant), but the
    live object is always the write-capable :class:`~blizzard.hub.store.internal.chunk_store.ChunkStore`.
    These component tests poke facts directly to set up a scenario the HTTP surface
    cannot reach on its own (parking a chunk mid-node without running it)."""
    return cast(IWriteChunkRepository, hub.services.chunks)


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
    run:
      - name: land
        command: land-the-repo
        produces: merged
    judgement:
      choices:
        success:
          description: Landed.
          to: done
        failure:
          description: Failed to land.
          to: build
"""


# --------------------------------------------------------------------------- #
# Unit — validation
# --------------------------------------------------------------------------- #


def _errors(yaml_nodes: dict) -> list[str]:
    doc = parse_graph_doc({"name": "g", "entry": "build", "nodes": yaml_nodes})
    return validate_graph(doc).errors


def test_hub_node_with_run_and_choices_is_valid() -> None:
    doc = parse_graph_doc(
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
                    "run": [{"command": "echo hi", "produces": "done-marker"}],
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
    result = validate_graph(doc)
    assert result.errors == []


def test_hub_node_with_run_rejects_prompt() -> None:
    errors = _errors(
        {
            "build": {
                "executor": "runner",
                "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "merge"}}},
            },
            "merge": {
                "executor": "hub",
                "prompt": "not allowed",
                "run": [{"command": "echo hi"}],
                "judgement": {"choices": {"success": {"description": "ok", "to": "done"}}},
            },
        }
    )
    assert any("must not declare `prompt`" in e for e in errors)


def test_hub_node_with_run_rejects_checks() -> None:
    errors = _errors(
        {
            "build": {
                "executor": "runner",
                "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "merge"}}},
            },
            "merge": {
                "executor": "hub",
                "checks": ["mise run test"],
                "run": [{"command": "echo hi"}],
                "judgement": {"choices": {"success": {"description": "ok", "to": "done"}}},
            },
        }
    )
    assert any("must not declare `checks`" in e for e in errors)


def test_hub_node_with_run_rejects_judgement_prompt() -> None:
    errors = _errors(
        {
            "build": {
                "executor": "runner",
                "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "merge"}}},
            },
            "merge": {
                "executor": "hub",
                "run": [{"command": "echo hi"}],
                "judgement": {"prompt": "assess it", "choices": {"success": {"description": "ok", "to": "done"}}},
            },
        }
    )
    assert any("must not declare `judgement.prompt`" in e for e in errors)


def test_hub_node_with_run_requires_a_judgement() -> None:
    errors = _errors(
        {
            "build": {
                "executor": "runner",
                "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "merge"}}},
            },
            "merge": {"executor": "hub", "run": [{"command": "echo hi"}]},
        }
    )
    assert any("must declare a judgement" in e for e in errors)


def test_run_on_a_runner_node_is_rejected() -> None:
    errors = _errors(
        {
            "build": {
                "executor": "runner",
                "run": [{"command": "echo hi"}],
                "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "done"}}},
            },
        }
    )
    assert any("`run:` is only legal on a hub node" in e for e in errors)


def test_a_bare_hub_node_with_no_run_and_no_judgement_is_rejected() -> None:
    """#67 retired the deliver special case: no node name is privileged any more —
    an ``executor: hub`` node must declare a judgement (its outcome choices) exactly
    like a generic hub command node does, whether or not it also declares ``run:``."""
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


# --------------------------------------------------------------------------- #
# Unit — pending outcome (#66)
# --------------------------------------------------------------------------- #


_BUILD_TO_MERGE = {
    "executor": "runner",
    "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "merge"}}},
}


def _merge_with(**overrides: object) -> dict:
    node = {
        "executor": "hub",
        "run": [{"command": "check-ci"}],
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


def test_pending_is_recognized_regardless_of_authored_choice_names() -> None:
    """The reserved ``pending`` outcome (#66) is recognized on its last stdout line
    even when no choice named it — like ``success``/``failure``, never an authored edge."""
    assert _printed_choice("doing work\npending", frozenset({"success", "failure"})) == HUB_PENDING_CHOICE
    assert _printed_choice("pending", frozenset()) == HUB_PENDING_CHOICE
    assert _printed_choice("unrelated-line", frozenset()) is None


def test_poll_interval_for_and_poll_timeout_for_default_when_unauthored() -> None:
    _, merge_node = _reified_merge_node()
    assert poll_interval_for(merge_node) == DEFAULT_POLL_INTERVAL
    assert poll_timeout_for(merge_node) == DEFAULT_POLL_TIMEOUT


def test_poll_interval_for_and_poll_timeout_for_honor_the_authored_override() -> None:
    doc = parse_graph_doc(
        {
            "name": "g",
            "entry": "merge",
            "nodes": {"merge": _merge_with(poll_interval=15, poll_timeout=90)},
        }
    )
    graph = reify_graph(doc, FixedClock(datetime(2026, 7, 17, tzinfo=UTC)))
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None
    assert poll_interval_for(merge_node) == timedelta(seconds=15)
    assert poll_timeout_for(merge_node) == timedelta(seconds=90)


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
    assert hub_node_pending(_facts_at_hub_node(polls=[])) is None


def test_hub_node_pending_returns_the_newest_matching_poll_fact() -> None:
    facts = _facts_at_hub_node(
        polls=[
            HubNodePollFact(node_id="nd_merge", epoch=1, polled_at=datetime(2026, 7, 17, 0, 0, tzinfo=UTC)),
            HubNodePollFact(node_id="nd_merge", epoch=1, polled_at=datetime(2026, 7, 17, 0, 1, tzinfo=UTC)),
        ]
    )
    pending = hub_node_pending(facts)
    assert pending is not None
    assert pending.polled_at == datetime(2026, 7, 17, 0, 1, tzinfo=UTC)


def test_hub_node_pending_ignores_a_poll_fact_from_a_stale_epoch() -> None:
    """A poll fact recorded for an earlier visit to the SAME node (before a later
    transition re-entered it) must not read as pending now — epoch-keyed, not
    node-keyed alone."""
    stale_poll = HubNodePollFact(node_id="nd_merge", epoch=1, polled_at=datetime(2026, 7, 17, tzinfo=UTC))
    facts = _facts_at_hub_node(epoch=2, polls=[stale_poll])
    assert hub_node_pending(facts) is None


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
    assert hub_node_pending(facts) is None


def test_hubnode_after_poll_before_slot_release_crash_point_is_registered() -> None:
    """The #66 between-polls crash point is declared, discoverable by the sweep."""
    names = {p.name for p in discover_crash_points()}
    assert "hubnode.after-poll.before-slot-release" in names


# --------------------------------------------------------------------------- #
# Unit — env injection
# --------------------------------------------------------------------------- #


def _reified_merge_node():  # type: ignore[no-untyped-def]
    doc = parse_graph_doc({"name": "g", "entry": "build", "nodes": _yaml_nodes()})
    graph = reify_graph(doc, FixedClock(datetime(2026, 7, 17, tzinfo=UTC)))
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None
    return graph, merge_node


def _yaml_nodes() -> dict:
    return yaml.safe_load(_HUB_CMD_GRAPH_YAML)["nodes"]


def test_build_hub_env_carries_no_model_credential_and_the_documented_keys() -> None:
    _, merge_node = _reified_merge_node()
    chunk = Chunk(chunk_id="ch_x", graph_id="gr_x", pm_pointers=[], minted_at=datetime(2026, 7, 17, tzinfo=UTC))
    artifact = ArtifactRow(
        kind=ArtifactKind.GIT_COMMIT,
        name="work",
        data="blizzard/ch-x:abc123",
        repo="acme/widget",
        artifact_id="art_1",
        chunk_id="ch_x",
        node_id="nd_build",
        node_name="build",
        epoch=1,
    )
    env = build_hub_env(
        HubEnvInputs(
            chunk=chunk,
            node=merge_node,
            workdir="/tmp/ch_x",
            epoch=1,
            artifacts=[artifact],
            base_branch="main",
            marker_callback_url="http://hub/api/chunks/ch_x/hub-markers",
            forge_url="http://forge",
            forge_token="tok",
        )
    )
    assert env["BZ_HUB_CHUNK_ID"] == "ch_x"
    assert env["BZ_HUB_WORKDIR"] == "/tmp/ch_x"
    assert env["BZ_HUB_NODE_NAME"] == "merge"
    assert env["BZ_HUB_EPOCH"] == "1"
    assert env["BZ_HUB_BASE_BRANCH"] == "main"
    assert "acme/widget" in env["BZ_HUB_GIT_COMMITS"]
    assert env["BZ_HUB_MARKER_CALLBACK_URL"] == "http://hub/api/chunks/ch_x/hub-markers"
    assert env["BZ_FORGE_URL"] == "http://forge"
    assert env["BZ_FORGE_TOKEN"] == "tok"
    assert "BZ_HUB_FEATURE_TITLE" not in env  # no feature_title given
    # Structurally agentless (`bzh:deterministic-shell`) — no key here ever names a
    # model/agent credential.
    forbidden = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "BZ_MODEL", "BZ_MODEL_API_KEY"}
    assert not forbidden & env.keys()


def test_build_hub_env_carries_the_feature_title_when_given() -> None:
    _, merge_node = _reified_merge_node()
    chunk = Chunk(chunk_id="ch_x", graph_id="gr_x", pm_pointers=[], minted_at=datetime(2026, 7, 17, tzinfo=UTC))
    env = build_hub_env(
        HubEnvInputs(
            chunk=chunk,
            node=merge_node,
            workdir="/tmp/ch_x",
            epoch=1,
            artifacts=[],
            base_branch="main",
            marker_callback_url="http://hub/api/chunks/ch_x/hub-markers",
            feature_title="Add rate limiting to the widget API",
        )
    )
    assert env["BZ_HUB_FEATURE_TITLE"] == "Add rate limiting to the widget API"


# --------------------------------------------------------------------------- #
# Component — the executor wired with fakes over a real hub store
# --------------------------------------------------------------------------- #


def _to_merge_node(hub, pointer=_POINTER, graph_yaml: str = _HUB_CMD_GRAPH_YAML):  # type: ignore[no-untyped-def]
    """Ingest, promote, claim, and complete ``build`` -> ``merge`` for one chunk.

    Returns ``(chunk_id, build_node_id, graph)``. The completion's own apply already
    runs the hub node executor synchronously (``apply.py``'s hub-node branch) — most
    tests below want that; the barrier test bypasses it (see its own helper)."""
    assert hub.client.post("/api/graphs", json={"definition_yaml": graph_yaml}).status_code == 201
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(pointer)]})
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
    chunk = hub.services.chunks.get(chunk_id)
    assert chunk is not None
    graph = hub.services.graphs.get(chunk.graph_id)
    assert graph is not None
    return chunk_id, build_node_id, graph


def _submit_build_pass(hub, chunk_id: str, build_node_id: str, epoch: int):  # type: ignore[no-untyped-def]
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
def test_produces_marker_skips_an_already_run_step(tmp_path: Path) -> None:
    runner = FakeHubCommandRunner()
    workdir = FakeHubWorkdir()
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=workdir)
    chunk_id, build_node_id, graph = _to_merge_node(hub)
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None

    # Pre-record the step's marker, as if a prior (crashed) run already completed it.
    _writable(hub).record_hub_artifact(
        chunk_id,
        node_id=merge_node.node_id,
        node_name="merge",
        epoch=1,
        name="merged",
        content="done",
        at=hub.clock.now(),
    )
    apply = _submit_build_pass(hub, chunk_id, build_node_id, 1)
    assert apply.json()["outcome"] == "hub_node_taken"

    # The step's own command never ran — skipped on the pre-existing marker.
    assert runner.calls == []
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "done"


@pytest.mark.component
def test_full_run_maps_success_to_the_authored_edge(tmp_path: Path) -> None:
    runner = FakeHubCommandRunner()
    workdir = FakeHubWorkdir()
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=workdir)
    chunk_id, build_node_id, _graph = _to_merge_node(hub)

    apply = _submit_build_pass(hub, chunk_id, build_node_id, 1)
    assert apply.json()["outcome"] == "hub_node_taken"

    assert len(runner.calls) == 1
    command, _cwd, env = runner.calls[0]
    assert command == "land-the-repo"
    assert env["BZ_HUB_CHUNK_ID"] == chunk_id
    # The chunk's PM pointer resolves through the default FakePmSource — its title
    # flows into the hub node's env for the land script to use as the PR/merge title.
    assert env["BZ_HUB_FEATURE_TITLE"] == "issue title"
    assert workdir.ensured == [chunk_id]

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "done"
    land_transitions = [t for t in detail["history"] if t["from_node_name"] == "merge"]
    assert len(land_transitions) == 1
    assert land_transitions[0]["choice_name"] == "success"
    assert land_transitions[0]["to_node_id"] == "done"  # the reserved terminal — no node to name


@pytest.mark.component
def test_a_failed_pm_fetch_degrades_the_feature_title_to_absent(tmp_path: Path) -> None:
    """A forge read failing while resolving the feature title must never break
    delivery (best-effort, #bzh design note above :meth:`HubNodeExecutor._resolve_feature_title`):
    the hub node still runs its ``run:`` step, just with no ``BZ_HUB_FEATURE_TITLE``."""
    runner = FakeHubCommandRunner()
    workdir = FakeHubWorkdir()
    hub = build_hub(
        tmp_path,
        hub_command_runner=runner,
        hub_workdir=workdir,
        pm={"default": FakePmSource(fail_refs={"42"})},
    )
    chunk_id, build_node_id, _graph = _to_merge_node(hub)

    apply = _submit_build_pass(hub, chunk_id, build_node_id, 1)
    assert apply.json()["outcome"] == "hub_node_taken"

    assert len(runner.calls) == 1
    _command, _cwd, env = runner.calls[0]
    assert "BZ_HUB_FEATURE_TITLE" not in env

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "done"


@pytest.mark.component
def test_nonzero_exit_maps_to_default_failure_edge(tmp_path: Path) -> None:
    runner = FakeHubCommandRunner()
    runner.arm("land-the-repo", CommandResult(exit_code=1, stdout="", stderr="boom"))
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir())
    chunk_id, build_node_id, _graph = _to_merge_node(hub)

    apply = _submit_build_pass(hub, chunk_id, build_node_id, 1)
    assert apply.json()["outcome"] == "hub_node_taken"

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    # Routed back to `build` (the `failure` edge's target) — running again, not done.
    assert detail["status"] == "running"
    assert detail["current_node_name"] == "build"


@pytest.mark.component
def test_a_printed_choice_overrides_the_exit_code_default(tmp_path: Path) -> None:
    """Exit 0 but the command prints `failure` on its last stdout line: the printed
    choice wins over the exit-0 default (#65's outcome-mapping vocabulary)."""
    runner = FakeHubCommandRunner()
    runner.arm("land-the-repo", CommandResult(exit_code=0, stdout="doing stuff\nfailure\n", stderr=""))
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir())
    chunk_id, build_node_id, _graph = _to_merge_node(hub)

    apply = _submit_build_pass(hub, chunk_id, build_node_id, 1)
    assert apply.json()["outcome"] == "hub_node_taken"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["current_node_name"] == "build"


def _submit_build_pass_with_commit(hub, chunk_id: str, build_node_id: str, epoch: int, *, repo: str):  # type: ignore[no-untyped-def]
    """Like :func:`_submit_build_pass`, but carrying one ``git_commit`` artifact for
    ``repo`` — the shape a hub node's kick-back accounting reads to tell a genuine
    delivery attempt (#64) from a bare hub-command smoke test."""
    return hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": epoch,
            "runner_id": "r1",
            "from_node_id": build_node_id,
            "check_results": [],
            "artifacts": [{"name": "w", "kind": "git_commit", "repo": repo, "branch_name": "b", "commit_hash": "c"}],
        },
    )


@pytest.mark.component
def test_a_non_terminal_route_with_nothing_landed_records_a_bounce(tmp_path: Path) -> None:
    """A hub node's outcome that routes back to a worker node while the repo it was
    handed never landed a ``merged/<repo>`` marker is a delivery kick-back (#64) — by
    the domain fact, never by choice name: ``failure`` here is the plain exit-code
    default, not a specially-recognized string."""
    runner = FakeHubCommandRunner()
    runner.arm("land-the-repo", CommandResult(exit_code=1, stdout="", stderr="boom"))
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir())
    chunk_id, build_node_id, _graph = _to_merge_node(hub)

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
    """The counterpart: every repo the node was handed already carries its
    ``merged/<repo>`` marker, so a non-terminal continuation (an authored
    ``landed -> <node>`` edge, #63) is forward progress, never a kick-back — no
    bounce fact, whatever the chosen outcome's name."""
    runner = FakeHubCommandRunner()
    runner.arm("land-the-repo", CommandResult(exit_code=0, stdout="landed\n", stderr=""))
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir())
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
    run:
      - name: land
        command: land-the-repo
        produces: merged
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
    # The repo already landed — a marker recorded ahead of this node's own run,
    # mirroring what the mid-run callback would have written during a real script's
    # push stage.
    _writable(hub).record_hub_artifact(
        chunk_id,
        node_id=merge_node.node_id,
        node_name="merge",
        epoch=1,
        name="merged/acme-widget",
        content="c",
        at=hub.clock.now(),
    )

    apply = _submit_build_pass_with_commit(hub, chunk_id, build_node_id, 1, repo="acme-widget")
    assert apply.json()["outcome"] == "hub_node_taken"

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["current_node_name"] == "verify"
    assert detail["bounces"] == []
    assert detail["landed"] is True


@pytest.mark.component
def test_stdout_stderr_are_captured_as_an_asset_artifact_visible_in_chunk_detail(tmp_path: Path) -> None:
    runner = FakeHubCommandRunner()
    runner.arm("land-the-repo", CommandResult(exit_code=0, stdout="merged cleanly", stderr=""))
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir())
    chunk_id, build_node_id, _graph = _to_merge_node(hub)

    _submit_build_pass(hub, chunk_id, build_node_id, 1)
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    logs = [a for a in detail["artifacts"] if a["name"].startswith("hub-log.")]
    assert logs, detail["artifacts"]
    assert "merged cleanly" in logs[0]["content"]


@pytest.mark.component
def test_mid_run_marker_callback_records_a_marker(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, hub_command_runner=FakeHubCommandRunner(), hub_workdir=FakeHubWorkdir())
    assert hub.client.post("/api/graphs", json={"definition_yaml": _HUB_CMD_GRAPH_YAML}).status_code == 201
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]})
    chunk_id = resp.json()["chunk_id"]
    chunk = hub.services.chunks.get(chunk_id)
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
def test_hub_advance_endpoint_no_ops_off_a_non_hub_command_node(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _HUB_CMD_GRAPH_YAML}).status_code == 201
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]})
    chunk_id = resp.json()["chunk_id"]
    result = hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    assert result.status_code == 200
    assert result.json()["ran"] is False


# --------------------------------------------------------------------------- #
# Component — the serialization barrier (REQUIRED, flagged)
# --------------------------------------------------------------------------- #


@pytest.mark.component
def test_serialization_barrier_two_chunks_never_run_hub_commands_concurrently(tmp_path: Path) -> None:
    """Two chunks parked at a hub command node both call the executor; the
    fleet-wide slot (#65) MUST ensure exactly one runs commands at a time.

    Chunk A's command blocks on a latch while holding the slot; chunk B's concurrent
    attempt must be DEFERRED (returns ``None``, no command run) rather than running
    alongside it — proven with a shared in-flight counter the fake runner maintains,
    never observed above 1."""
    runner = FakeHubCommandRunner()
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir())
    assert hub.client.post("/api/graphs", json={"definition_yaml": _HUB_CMD_GRAPH_YAML}).status_code == 201

    def _ingest_claim_and_park_at_merge(pointer_ref: str) -> tuple:  # type: ignore[no-untyped-def]
        resp = hub.client.post(
            "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": pointer_ref})]}
        )
        chunk_id = resp.json()["chunk_id"]
        assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
        claim = hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
        )
        build_node_id = claim.json()["envelope"]["node"]["node_id"]
        report_lease(hub, chunk_id, epoch=1, seq=1)
        chunk = hub.services.chunks.get(chunk_id)
        assert chunk is not None
        graph = hub.services.graphs.get(chunk.graph_id)
        assert graph is not None
        merge_node = graph.node_by_name("merge")
        assert merge_node is not None
        build_node = graph.node_by_id(build_node_id)
        assert build_node is not None
        # Park directly at `merge`, bypassing the executor's own auto-run on
        # transition — the shape "two held chunks both poll hub-advance" needs.
        _writable(hub).record_transition(
            transition_id=f"tr_test_{pointer_ref}",
            chunk_id=chunk_id,
            from_node_id=build_node.node_id,
            to_node_id=merge_node.node_id,
            choice_name="pass",
            epoch=1,
            runner_id="r1",
            at=hub.clock.now(),
            artifacts=[],
        )
        return chunk, graph, merge_node

    chunk_a, graph_a, merge_node = _ingest_claim_and_park_at_merge("100")
    chunk_b, _graph_b, _ = _ingest_claim_and_park_at_merge("200")

    entered = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0

    def before_run(_command: str) -> None:
        nonlocal in_flight, max_in_flight
        with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        entered.set()
        release.wait(timeout=5)
        with lock:
            in_flight -= 1

    runner.before_run = before_run

    result_a: dict = {}

    def run_a() -> None:
        result_a["value"] = hub.services.hub_node.run(chunk_a, graph_a, merge_node, epoch=1)

    thread = threading.Thread(target=run_a)
    thread.start()
    assert entered.wait(timeout=5), "chunk A never entered its command"

    # Chunk B's concurrent attempt: the slot is live (held by chunk A) — deferred,
    # not run alongside it.
    result_b = hub.services.hub_node.run(chunk_b, graph_a, merge_node, epoch=1)
    assert result_b is None

    release.set()
    thread.join(timeout=5)

    assert result_a["value"] is not None
    assert max_in_flight == 1, "two chunks' hub commands ran concurrently — serialization slot failed"

    # Now that A released the slot, B can run to completion on its own call.
    result_b2 = hub.services.hub_node.run(chunk_b, graph_a, merge_node, epoch=1)
    assert result_b2 is not None
    assert len(runner.calls) == 2


# --------------------------------------------------------------------------- #
# Component — pending outcome (#66): polls without blocking the queue
# --------------------------------------------------------------------------- #

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
    run:
      - name: check-ci
        command: check-ci
    judgement:
      choices:
        success:
          description: Green.
          to: done
        failure:
          description: Red.
          to: build
"""


def _to_poll_merge_node(hub, pointer):  # type: ignore[no-untyped-def]
    """Same shape as ``_to_merge_node`` but minting the pending-capable poll graph."""
    assert hub.client.post("/api/graphs", json={"definition_yaml": _POLL_GRAPH_YAML}).status_code == 201
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(pointer)]})
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
    chunk = hub.services.chunks.get(chunk_id)
    assert chunk is not None
    graph = hub.services.graphs.get(chunk.graph_id)
    assert graph is not None
    return chunk_id, build_node_id, graph


@pytest.mark.component
def test_pending_records_no_transition_and_releases_the_slot(tmp_path: Path) -> None:
    """A ``pending`` outcome parks the chunk without a transition, and the fleet-wide
    slot is free immediately after — the #66 property a pending node must hold."""
    runner = FakeHubCommandRunner()
    runner.arm("check-ci", CommandResult(exit_code=0, stdout="pending", stderr=""))
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir())
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
    assert _writable(hub).count_live_hub_exec_slots() == 0


@pytest.mark.component
def test_hub_advance_respects_the_poll_interval_then_succeeds(tmp_path: Path) -> None:
    """Re-polling before ``poll_interval`` elapses is a no-op (the command never
    re-runs); once elapsed, the node re-runs and a subsequent success routes ``done``."""
    runner = FakeHubCommandRunner()
    runner.arm(
        "check-ci",
        CommandResult(exit_code=0, stdout="pending", stderr=""),
        CommandResult(exit_code=0, stdout="success", stderr=""),
    )
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir())
    chunk_id, build_node_id, _graph = _to_poll_merge_node(hub, {"source": "default", "ref": "poll-2"})

    apply = _submit_build_pass(hub, chunk_id, build_node_id, 1)
    assert apply.json()["outcome"] == "hub_node_taken"
    assert len(runner.calls) == 1

    # Not yet due (poll_interval: 30) — hub-advance no-ops, the command does not re-run.
    too_soon = hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    assert too_soon.json()["ran"] is False
    assert len(runner.calls) == 1

    hub.clock.advance(timedelta(seconds=31))
    due = hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    assert due.json()["ran"] is True
    assert due.json()["outcome_choice"] == "success"
    assert len(runner.calls) == 2

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "done"
    assert detail["pending"] is None


@pytest.mark.component
def test_pending_chunk_does_not_block_another_chunks_hub_node(tmp_path: Path) -> None:
    """The key #66 property: chunk A parked pending (slot released) must not prevent
    chunk B's hub node from running to completion on its own hub-advance call."""
    runner = FakeHubCommandRunner()
    runner.arm("check-ci", CommandResult(exit_code=0, stdout="pending", stderr=""))
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir())

    chunk_a, build_a, _graph_a = _to_poll_merge_node(hub, {"source": "default", "ref": "poll-a"})
    apply_a = _submit_build_pass(hub, chunk_a, build_a, 1)
    assert apply_a.json()["outcome"] == "hub_node_taken"
    detail_a = hub.client.get(f"/api/chunks/{chunk_a}").json()
    assert detail_a["pending"] is not None  # parked, slot released

    # Chunk B's own run of the same command is armed to succeed outright.
    runner.script["check-ci"] = [CommandResult(exit_code=0, stdout="success", stderr="")]
    chunk_b, build_b, _graph_b = _to_poll_merge_node(hub, {"source": "default", "ref": "poll-b"})
    apply_b = _submit_build_pass(hub, chunk_b, build_b, 1)
    assert apply_b.json()["outcome"] == "hub_node_taken"

    detail_b = hub.client.get(f"/api/chunks/{chunk_b}").json()
    assert detail_b["status"] == "done"  # ran to completion, unblocked by A's pending park

    # A is still parked pending, unaffected.
    detail_a_again = hub.client.get(f"/api/chunks/{chunk_a}").json()
    assert detail_a_again["pending"] is not None


@pytest.mark.component
def test_poll_timeout_routes_the_failure_edge_via_the_kickback_path(tmp_path: Path) -> None:
    """Pending past ``poll_timeout`` stops polling and kicks back via #64's bounce
    accounting — a bounce fact recorded, routed to the ``failure`` edge's target
    (``build``), below the fleet-wide default ``bounce_cap`` so no escalation yet."""
    runner = FakeHubCommandRunner()
    runner.arm("check-ci", CommandResult(exit_code=0, stdout="pending", stderr=""))
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir())
    chunk_id, build_node_id, _graph = _to_poll_merge_node(hub, {"source": "default", "ref": "poll-timeout"})

    apply = _submit_build_pass(hub, chunk_id, build_node_id, 1)
    assert apply.json()["outcome"] == "hub_node_taken"  # poll #1 at t0

    hub.clock.advance(timedelta(seconds=31))
    hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")  # poll #2 at t0+31
    hub.clock.advance(timedelta(seconds=31))
    hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")  # poll #3 at t0+62
    assert len(runner.calls) == 3

    # poll_timeout: 90, measured from the FIRST poll (t0) — elapsed is now 62 + 31 = 93 >= 90.
    hub.clock.advance(timedelta(seconds=31))
    timed_out = hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    assert timed_out.json()["ran"] is True
    assert timed_out.json()["outcome_choice"] == "failure"
    # The command itself is NOT re-run a 4th time — the timeout check pre-empts it.
    assert len(runner.calls) == 3

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "running"
    assert detail["current_node_name"] == "build"
    assert detail["pending"] is None
    assert len(detail["bounces"]) == 1
    assert detail["bounces"][0]["cause"] == "poll-timeout"
    assert _writable(hub).count_live_hub_exec_slots() == 0


@pytest.mark.component
def test_poll_timeout_escalates_once_the_bounce_cap_is_crossed(tmp_path: Path) -> None:
    """Repeated poll-timeouts are bounces (#64): crossing ``bounce_cap`` escalates
    ``needs_human`` instead of routing back, exactly as a repeated conflict would."""
    runner = FakeHubCommandRunner()
    runner.arm("check-ci", CommandResult(exit_code=0, stdout="pending", stderr=""))
    graph_yaml = _POLL_GRAPH_YAML.replace("poll_timeout: 90", "poll_timeout: 30\n    bounce_cap: 1")
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir())
    assert hub.client.post("/api/graphs", json={"definition_yaml": graph_yaml}).status_code == 201
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "poll-cap"})]})
    chunk_id = resp.json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    build_node_id = claim.json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)

    # Bounce #1 (poll_timeout: 30, one poll at t0 then straight past the bound).
    _submit_build_pass(hub, chunk_id, build_node_id, 1)
    hub.clock.advance(timedelta(seconds=31))
    first_timeout = hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    assert first_timeout.json()["outcome_choice"] == "failure"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "running"  # below the cap (1) — routed back, not escalated
    assert len(detail["bounces"]) == 1

    # Re-submit build -> merge a second time (bounce_cap: 1 means bounce #2 escalates).
    # Advance the clock first: the kickback's arrival transition and this next
    # departure transition share the same fencing epoch (2), and `newest_transition`
    # tie-breaks same-epoch transitions by `recorded_at` — a FixedClock tick apart
    # keeps them distinguishable, exactly as real wall-clock instants always are.
    hub.clock.advance(timedelta(seconds=1))
    report_lease(hub, chunk_id, epoch=2, seq=2)
    second_build_node = hub.client.get(f"/api/chunks/{chunk_id}").json()["current_node_id"]
    _submit_build_pass(hub, chunk_id, second_build_node, 2)
    hub.clock.advance(timedelta(seconds=31))
    second_timeout = hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    assert second_timeout.json()["outcome_choice"] == "failure"
    detail2 = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail2["status"] == "needs_human"
    assert len(detail2["bounces"]) == 2
