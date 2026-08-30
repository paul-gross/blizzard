"""The hub backstop and the runner gate agree on the checks gate (component tier, issue #114).

Both sides call the one shared predicate ``ChecksGate.violated`` rather than each
re-derive "is a gated choice red?" inline; this drives both real decision sites over
one scenario matrix and asserts they reach the same verdict."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.checks import CheckOutcome
from blizzard.runner.loop.steps import Advance, Pull
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse
from tests.runner_fakes import (
    FakeCheckRunner,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    make_context,
    make_envelope,
    make_store,
)
from tests.support import build_hub, pointer_token, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "9"}
_CHOICES = [("pass", "complete and green"), ("fail", "incomplete")]

#: (id, requires_checks on `pass`, check results, expected: does the gate reject a `pass`?)
#: — rejected iff gated and any check is red.
_SCENARIOS = [
    ("gated-all-green", True, [("a", True), ("b", True)], False),
    ("gated-one-red", True, [("a", True), ("b", False)], True),
    ("gated-all-red", True, [("a", False)], True),
    ("ungated-red-is-fine", False, [("a", False)], False),
    ("ungated-green-is-fine", False, [("a", True)], False),
]


def _graph_yaml(requires_checks: bool) -> str:
    gate = "\n          requires_checks: true" if requires_checks else ""
    return f"""
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build.
    checks:
      - a
      - b
    judgement:
      prompt: |
        Assess.
      choices:
        pass:
          description: Complete and green.
          to: deliver{gate}
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
          description: Failed.
          to: build
"""


def _hub_rejects(tmp_path: Path, requires_checks: bool, results: list[tuple[str, bool]]) -> bool:
    tmp_path.mkdir(parents=True, exist_ok=True)
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _graph_yaml(requires_checks)}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    node_id = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": node_id,
            "check_results": [{"command": c, "passed": p} for c, p in results],
            "artifacts": [
                {"name": "w", "kind": "git_commit", "repo": "acme/w", "branch_name": "b", "commit_hash": "c"}
            ],
        },
    )
    return resp.json()["outcome"] == "failure"


def _runner_fails(tmp_path: Path, requires_checks: bool, results: list[tuple[str, bool]]) -> bool:
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(datetime(2026, 7, 25, tzinfo=UTC))
    store.record_lease(
        NewLease(
            lease_id="lease_b",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=clock.now(),
        )
    )
    store.record_spawn("lease_b", pid=100, process_start_time="s", session_id="sess-a", spawned_at=clock.now())
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=clock.now())

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1",
        "build",
        node_id="nd_build",
        choices=_CHOICES,
        checks=["a", "b"],
        requires_checks={"pass"} if requires_checks else set(),
    )
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.DONE)]
    harness = FakeHarness(handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="s"), verdict="pass")
    check_runner = FakeCheckRunner({c: CheckOutcome(passed=p, output_tail="") for c, p in results})
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        check_runner=check_runner,
        clock=clock,
    )
    Advance(ctx).run()
    Pull(ctx).run()
    # A gate failure buffers no completion; an accepted `pass` buffers exactly one.
    return hub.completions == []


@pytest.mark.parametrize(
    ("requires_checks", "results", "expected_reject"),
    [pytest.param(rc, r, e, id=i) for i, rc, r, e in _SCENARIOS],
)
def test_hub_and_runner_agree_on_the_checks_gate(
    tmp_path: Path, requires_checks: bool, results: list[tuple[str, bool]], expected_reject: bool
) -> None:
    """One scenario, both real decision sites, same verdict — and the verdict is expected."""
    hub_rejects = _hub_rejects(tmp_path / "hub", requires_checks, results)
    runner_fails = _runner_fails(tmp_path / "runner", requires_checks, results)

    assert hub_rejects == runner_fails, (
        f"checks-gate drift: the hub backstop {'rejects' if hub_rejects else 'accepts'} this "
        f"submission while the runner {'fences' if runner_fails else 'accepts'} it — the two must "
        f"share `ChecksGate` so a worker the runner fences is never let through by the hub."
    )
    assert hub_rejects is expected_reject
    assert runner_fails is expected_reject
