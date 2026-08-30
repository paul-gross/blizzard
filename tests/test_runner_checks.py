"""ADVANCE runs a node's ``checks:`` at worker exit and records durable facts (issue #114).

A real ADVANCE tick against a real tmp store proves checks run at worker exit, before
judgement, in the declared cwd, and each result is a durable fact surviving a runner
kill. Plus unit coverage of ``Judgement.checks``'s idempotency and the store round trip.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.domain.checks import CheckResultRecord
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.checks import DEFAULT_CHECK_TIMEOUT, CheckOutcome
from blizzard.runner.loop.judgement import Judgement
from blizzard.runner.loop.judgement_prompt import JudgementPrompt
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

_NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


def _prompt(results: list[CheckResultRecord]) -> JudgementPrompt:
    return JudgementPrompt(make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES), results)


def _seed_exited_lease(
    store, *, lease_id: str, chunk_id: str, node_id: str, epoch: int, workdir: str = "/ws/e1"
) -> None:
    store.record_lease(
        NewLease(
            lease_id=lease_id,
            chunk_id=chunk_id,
            graph_id="gr_1",
            node_id=node_id,
            node_name="build",
            epoch=epoch,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(lease_id, pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_binding(chunk_id=chunk_id, environment_id="e1", workdir=workdir, bound_at=_NOW)


# --- Component: the full ADVANCE path ------------------------------------------


@pytest.mark.component
def test_advance_runs_checks_records_facts_and_injects_them_into_the_submission(tmp_path: Path) -> None:
    """A node with ``checks:`` runs them at exit, records each as a durable fact, and
    carries ``(command, passed)`` on the completion (AC #1/#2)."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(_NOW)
    _seed_exited_lease(store, lease_id="lease_b", chunk_id="ch_1", node_id="nd_build", epoch=1, workdir="/ws/e1")

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "build", node_id="nd_build", choices=_CHOICES, checks=["mise run lint", "mise run test"]
    )
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.DONE)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"), verdict="pass"
    )
    check_runner = FakeCheckRunner()  # every check green by default
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

    # Both checks ran, in the env workdir root (no checks_cwd), under the default timeout.
    assert [c[0] for c in check_runner.calls] == ["mise run lint", "mise run test"]
    assert all(c[1] == "/ws/e1" and c[2] == DEFAULT_CHECK_TIMEOUT for c in check_runner.calls)

    # Durable facts survive to be read back (AC #2).
    assert store.checks_ran("lease_b", 1) is True
    records = store.check_results_for_lease("lease_b", 1)
    assert [(r.command, r.passed) for r in records] == [("mise run lint", True), ("mise run test", True)]

    # Injected onto the completion (command, passed only — output_tail stays runner-local).
    _, submission = hub.completions[0]
    assert [(cr.command, cr.passed) for cr in submission.check_results] == [
        ("mise run lint", True),
        ("mise run test", True),
    ]


@pytest.mark.component
def test_advance_records_a_red_check_and_still_buffers_the_completion(tmp_path: Path) -> None:
    """A red check is recorded (passed=False) and — with no gating yet (Phase 2) — the
    worker's chosen edge still buffers. The tail is captured on the durable fact."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(_NOW)
    _seed_exited_lease(store, lease_id="lease_b", chunk_id="ch_1", node_id="nd_build", epoch=1)

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "build", node_id="nd_build", choices=_CHOICES, checks=["mise run test"]
    )
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.DONE)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"), verdict="fail"
    )
    check_runner = FakeCheckRunner({"mise run test": CheckOutcome(passed=False, output_tail="2 failed")})
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

    records = store.check_results_for_lease("lease_b", 1)
    assert [(r.command, r.passed, r.output_tail) for r in records] == [("mise run test", False, "2 failed")]
    _, submission = hub.completions[0]
    assert submission.choice == "fail"
    assert [(cr.command, cr.passed) for cr in submission.check_results] == [("mise run test", False)]


@pytest.mark.component
def test_advance_with_no_checks_runs_nothing_and_carries_empty_check_results(tmp_path: Path) -> None:
    """A node with no ``checks:`` runs no check subprocess, records no facts, and carries
    empty ``check_results`` (AC #6 injection-only half)."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(_NOW)
    _seed_exited_lease(store, lease_id="lease_b", chunk_id="ch_1", node_id="nd_build", epoch=1)

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.DONE)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"), verdict="pass"
    )
    check_runner = FakeCheckRunner()
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

    assert check_runner.calls == []
    assert store.checks_ran("lease_b", 1) is False
    _, submission = hub.completions[0]
    assert submission.check_results == []


# --- Unit: the check step in isolation, faked seam -----------------------------


def _ctx_for_unit(store, *, check_runner: FakeCheckRunner, clock: FixedClock):
    return make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=WorkerHandle(session_id="s", pid=1, process_start_time="t"), verdict="pass"),
        probe=FakeProbe(),
        check_runner=check_runner,
        clock=clock,
    )


def _lease_and_bindings(store, *, checks_cwd=None, checks_timeout=None):
    _seed_exited_lease(store, lease_id="lease_b", chunk_id="ch_1", node_id="nd_build", epoch=1, workdir="/ws/e1")
    lease = store.lease("lease_b")
    assert lease is not None
    bindings = store.bindings_for_chunk("ch_1")
    envelope = make_envelope(
        "ch_1",
        "build",
        node_id="nd_build",
        choices=_CHOICES,
        epoch=1,
        checks=["mise run lint"],
        checks_cwd=checks_cwd,
        checks_timeout=checks_timeout,
    )
    return lease, bindings, envelope


@pytest.mark.unit
def test_checks_is_idempotent_reading_back_without_rerunning(tmp_path: Path) -> None:
    """First call runs the checks and records them; a second call on the same
    ``(lease, epoch)`` reads the recorded facts back and never re-invokes the seam — the
    crash-recovery re-drive contract."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    check_runner = FakeCheckRunner({"mise run lint": CheckOutcome(passed=True, output_tail="ok")})
    ctx = _ctx_for_unit(store, check_runner=check_runner, clock=FixedClock(_NOW))
    lease, bindings, envelope = _lease_and_bindings(store)

    first = Judgement(ctx, lease, envelope, bindings).checks()
    assert [(r.command, r.passed, r.output_tail) for r in first] == [("mise run lint", True, "ok")]
    assert len(check_runner.calls) == 1
    assert store.checks_ran("lease_b", 1) is True

    second = Judgement(ctx, lease, envelope, bindings).checks()
    assert [(r.command, r.passed, r.output_tail) for r in second] == [("mise run lint", True, "ok")]
    assert len(check_runner.calls) == 1, "a re-drive must read the recorded facts back, not re-run the checks"


@pytest.mark.unit
def test_checks_resolves_checks_cwd_relative_to_the_binding_workdir(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    check_runner = FakeCheckRunner()
    ctx = _ctx_for_unit(store, check_runner=check_runner, clock=FixedClock(_NOW))
    lease, bindings, envelope = _lease_and_bindings(store, checks_cwd="blizzard", checks_timeout=42)

    Judgement(ctx, lease, envelope, bindings).checks()

    assert check_runner.calls == [("mise run lint", "/ws/e1/blizzard", 42)]


@pytest.mark.unit
def test_checks_is_empty_and_runs_nothing_for_a_node_with_no_checks(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    check_runner = FakeCheckRunner()
    ctx = _ctx_for_unit(store, check_runner=check_runner, clock=FixedClock(_NOW))
    _seed_exited_lease(store, lease_id="lease_b", chunk_id="ch_1", node_id="nd_build", epoch=1)
    lease = store.lease("lease_b")
    assert lease is not None
    bindings = store.bindings_for_chunk("ch_1")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES, epoch=1)  # no checks

    assert Judgement(ctx, lease, envelope, bindings).checks() == []
    assert check_runner.calls == []
    assert store.checks_ran("lease_b", 1) is False


# --- Unit: the store's check-result read/write ---------------------------------


@pytest.mark.unit
def test_record_check_results_round_trips_and_is_latest_wins_on_a_rerun(tmp_path: Path) -> None:
    """A recovery re-run (same ``(lease, epoch)``) replaces the prior rows rather than
    accumulating duplicates — ``check_results_for_lease`` reads exactly one run's rows."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    store.record_check_results(
        lease_id="lease_b",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        results=[CheckResultRecord(command="c1", passed=False, output_tail="red")],
        at=_NOW,
    )
    assert [(r.command, r.passed) for r in store.check_results_for_lease("lease_b", 1)] == [("c1", False)]

    # Re-run (a crash after rows, before marker → recovery re-runs) — latest-wins overwrite.
    store.record_check_results(
        lease_id="lease_b",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        results=[CheckResultRecord(command="c1", passed=True, output_tail="green")],
        at=_NOW,
    )
    assert [(r.command, r.passed, r.output_tail) for r in store.check_results_for_lease("lease_b", 1)] == [
        ("c1", True, "green")
    ]


@pytest.mark.unit
def test_checks_ran_marker_is_idempotent(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    assert store.checks_ran("lease_b", 1) is False
    store.record_checks_ran(lease_id="lease_b", epoch=1, at=_NOW)
    store.record_checks_ran(lease_id="lease_b", epoch=1, at=_NOW)  # a genuine replay
    assert store.checks_ran("lease_b", 1) is True


# --- Phase 3: injection of check results into the judgement prompt -------------


@pytest.mark.unit
def test_checks_block_renders_pass_and_fail_and_shows_the_tail_only_on_failure() -> None:
    block = _prompt(
        [
            CheckResultRecord(command="mise run lint", passed=True, output_tail="all good"),
            CheckResultRecord(command="mise run test", passed=False, output_tail="assert 1 == 2\n1 failed"),
        ]
    )._checks_block()
    # Harness-inert: every line is `#`-prefixed so a mock harness that execs the prompt
    # sees only comments.
    assert all(line.startswith("#") for line in block.splitlines() if line)
    assert "[PASS] mise run lint" in block
    assert "[FAIL] mise run test" in block
    # The failing check's tail is shown (the worker needs the why); the passing one's is not.
    assert "assert 1 == 2" in block
    assert "1 failed" in block
    assert "all good" not in block


@pytest.mark.unit
def test_checks_block_is_empty_for_no_checks() -> None:
    assert _prompt([])._checks_block() == ""


@pytest.mark.component
def test_advance_injects_the_check_results_into_the_judgement_prompt(tmp_path: Path) -> None:
    """The rendered check block reaches the judge elicitation between the authored prose and
    the ``<Choice>`` tail (AC #3)."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(_NOW)
    _seed_exited_lease(store, lease_id="lease_b", chunk_id="ch_1", node_id="nd_build", epoch=1)

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "build", node_id="nd_build", choices=_CHOICES, checks=["mise run test"]
    )
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.DONE)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"), verdict="fail"
    )
    check_runner = FakeCheckRunner({"mise run test": CheckOutcome(passed=False, output_tail="2 failed")})
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

    # The single judge elicitation carries the check block, between the prose and the tail.
    assert len(harness.judged) == 1
    _, _, judge_prompt = harness.judged[0]
    assert "[FAIL] mise run test" in judge_prompt
    assert "2 failed" in judge_prompt
    assert judge_prompt.index("Assess the build.") < judge_prompt.index("[FAIL]") < judge_prompt.index("<Choice>")


# --- Phase 4: the checks gate (runner-local) -----------------------------------


@pytest.mark.unit
def test_checks_gate_violated_predicate() -> None:
    from blizzard.wire.completion import CheckResult, ChecksGate

    green = [CheckResult(command="a", passed=True), CheckResult(command="b", passed=True)]
    red = [CheckResult(command="a", passed=True), CheckResult(command="b", passed=False)]
    # Ungated: never violated, whatever the checks say.
    assert ChecksGate(False, red).violated is False
    assert ChecksGate(False, green).violated is False
    # Gated: violated iff any check is red.
    assert ChecksGate(True, green).violated is False
    assert ChecksGate(True, red).violated is True
    # Gated with no results is vacuously satisfied (the validator forbids this shape anyway).
    assert ChecksGate(True, []).violated is False


@pytest.mark.component
def test_advance_gates_a_requires_checks_pass_with_a_red_check_and_consumes_a_retry(tmp_path: Path) -> None:
    """Selecting a ``requires_checks`` choice while a check is red is treated like an
    unparseable verdict — no completion buffers, a retry is consumed (AC #4)."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_exited_lease(store, lease_id="lease_b", chunk_id="ch_1", node_id="nd_build", epoch=1)

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "build", node_id="nd_build", choices=_CHOICES, checks=["mise run test"], requires_checks={"pass"}
    )
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"), verdict="pass"
    )
    check_runner = FakeCheckRunner({"mise run test": CheckOutcome(passed=False, output_tail="boom")})
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        check_runner=check_runner,
        clock=FixedClock(_NOW),
    )

    Advance(ctx).run()
    Pull(ctx).run()

    # The gated `pass` over a red check never buffers a completion — it failed the attempt.
    assert hub.completions == []
    # The check facts were still recorded (they inform the re-attempt's judgement).
    assert [(r.command, r.passed) for r in store.check_results_for_lease("lease_b", 1)] == [("mise run test", False)]


@pytest.mark.component
def test_advance_lets_a_red_check_route_through_a_non_gated_fail(tmp_path: Path) -> None:
    """A red check reported through a non-gated choice (`fail`) routes normally — the gate
    never fires (AC #5)."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_exited_lease(store, lease_id="lease_b", chunk_id="ch_1", node_id="nd_build", epoch=1)

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "build", node_id="nd_build", choices=_CHOICES, checks=["mise run test"], requires_checks={"pass"}
    )
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.DONE)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"), verdict="fail"
    )
    check_runner = FakeCheckRunner({"mise run test": CheckOutcome(passed=False, output_tail="boom")})
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        check_runner=check_runner,
        clock=FixedClock(_NOW),
    )

    Advance(ctx).run()
    Pull(ctx).run()

    # The non-gated `fail` buffers and applies normally, red check notwithstanding.
    assert len(hub.completions) == 1
    _, submission = hub.completions[0]
    assert submission.choice == "fail"


@pytest.mark.component
def test_advance_accepts_a_requires_checks_pass_when_checks_are_green(tmp_path: Path) -> None:
    """A gated ``pass`` over green checks routes normally — the gate only fences red (AC #4 accept)."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_exited_lease(store, lease_id="lease_b", chunk_id="ch_1", node_id="nd_build", epoch=1)

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "build", node_id="nd_build", choices=_CHOICES, checks=["mise run test"], requires_checks={"pass"}
    )
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.DONE)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"), verdict="pass"
    )
    check_runner = FakeCheckRunner()  # green
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        check_runner=check_runner,
        clock=FixedClock(_NOW),
    )

    Advance(ctx).run()
    Pull(ctx).run()

    assert len(hub.completions) == 1
    assert hub.completions[0][1].choice == "pass"
