"""ADVANCE runs a node's ``checks:`` at worker exit and records durable facts.

A real ADVANCE tick against a real tmp store proves checks run at worker exit, before
judgement, in the declared cwd, and each result is recorded as a durable fact in the store.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import ApplyOutcome
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.leases.model import NewLease
from blizzard.runner.lifecycle.judgement.check_runner import DEFAULT_CHECK_TIMEOUT, CheckOutcome
from blizzard.runner.loop.steps import Advance, Pull
from blizzard.runner.node_steps.submissions import ApplyReply
from tests.runner_fakes import (
    FakeCheckRunner,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    make_context,
    make_envelope,
    make_store,
)

_NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


def _seed_exited_lease(
    store: SqlAlchemyRunnerStore, *, lease_id: str, chunk_id: str, node_id: str, epoch: int, workdir: str = "/ws/e1"
) -> None:
    store.record_lease(
        NewLease(
            lease_id=lease_id,
            chunk_id=chunk_id,
            graph_id="gr_1",
            node_id=node_id,
            node_name="build",
            epoch=epoch,
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        lease_id,
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id=chunk_id, environment_id="e1", workdir=workdir, bound_at=_NOW)


# --- Component: the full ADVANCE path ------------------------------------------


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
    hub.apply_responses = [ApplyReply(outcome=ApplyOutcome.DONE)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100), verdict="pass"
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

    Advance(ctx).run()  # launches the detached elicitation
    Advance(ctx).run()  # collects it — the fake pid reads dead by default
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


def test_advance_records_a_red_check_and_still_buffers_the_completion(tmp_path: Path) -> None:
    """A red check is recorded (passed=False) and, with the
    choice not gated on checks, the worker's chosen edge still buffers. The tail is captured on the durable fact."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(_NOW)
    _seed_exited_lease(store, lease_id="lease_b", chunk_id="ch_1", node_id="nd_build", epoch=1)

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "build", node_id="nd_build", choices=_CHOICES, checks=["mise run test"]
    )
    hub.apply_responses = [ApplyReply(outcome=ApplyOutcome.DONE)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100), verdict="fail"
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

    Advance(ctx).run()  # launches the detached elicitation
    Advance(ctx).run()  # collects it — the fake pid reads dead by default
    Pull(ctx).run()

    records = store.check_results_for_lease("lease_b", 1)
    assert [(r.command, r.passed, r.output_tail) for r in records] == [("mise run test", False, "2 failed")]
    _, submission = hub.completions[0]
    assert submission.choice == "fail"
    assert [(cr.command, cr.passed) for cr in submission.check_results] == [("mise run test", False)]


def test_advance_with_no_checks_runs_nothing_and_carries_empty_check_results(tmp_path: Path) -> None:
    """A node with no ``checks:`` runs no check subprocess, records no facts, and carries
    empty ``check_results`` (AC #6 injection-only half)."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(_NOW)
    _seed_exited_lease(store, lease_id="lease_b", chunk_id="ch_1", node_id="nd_build", epoch=1)

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    hub.apply_responses = [ApplyReply(outcome=ApplyOutcome.DONE)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100), verdict="pass"
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

    Advance(ctx).run()  # launches the detached elicitation
    Advance(ctx).run()  # collects it — the fake pid reads dead by default
    Pull(ctx).run()

    assert check_runner.calls == []
    assert store.checks_ran("lease_b", 1) is False
    _, submission = hub.completions[0]
    assert submission.check_results == []


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
    hub.apply_responses = [ApplyReply(outcome=ApplyOutcome.DONE)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100), verdict="fail"
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


def test_advance_gates_a_requires_checks_pass_with_a_red_check_and_consumes_a_retry(tmp_path: Path) -> None:
    """Selecting a ``requires_checks`` choice while a check is red is treated like an
    unparseable verdict — no completion buffers, and the red check is still recorded."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_exited_lease(store, lease_id="lease_b", chunk_id="ch_1", node_id="nd_build", epoch=1)

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "build", node_id="nd_build", choices=_CHOICES, checks=["mise run test"], requires_checks={"pass"}
    )
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100), verdict="pass"
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

    Advance(ctx).run()  # launches the detached elicitation
    Advance(ctx).run()  # collects it — the fake pid reads dead by default
    Pull(ctx).run()

    # The gated `pass` over a red check never buffers a completion — it failed the attempt.
    assert hub.completions == []
    # The check facts were still recorded (they inform the re-attempt's judgement).
    assert [(r.command, r.passed) for r in store.check_results_for_lease("lease_b", 1)] == [("mise run test", False)]


def test_advance_lets_a_red_check_route_through_a_non_gated_fail(tmp_path: Path) -> None:
    """A red check reported through a non-gated choice (`fail`) routes normally — the gate
    never fires (AC #5)."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_exited_lease(store, lease_id="lease_b", chunk_id="ch_1", node_id="nd_build", epoch=1)

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "build", node_id="nd_build", choices=_CHOICES, checks=["mise run test"], requires_checks={"pass"}
    )
    hub.apply_responses = [ApplyReply(outcome=ApplyOutcome.DONE)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100), verdict="fail"
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

    Advance(ctx).run()  # launches the detached elicitation
    Advance(ctx).run()  # collects it — the fake pid reads dead by default
    Pull(ctx).run()

    # The non-gated `fail` buffers and applies normally, red check notwithstanding.
    assert len(hub.completions) == 1
    _, submission = hub.completions[0]
    assert submission.choice == "fail"


def test_advance_accepts_a_requires_checks_pass_when_checks_are_green(tmp_path: Path) -> None:
    """A gated ``pass`` over green checks routes normally — the gate only fences red (AC #4 accept)."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_exited_lease(store, lease_id="lease_b", chunk_id="ch_1", node_id="nd_build", epoch=1)

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "build", node_id="nd_build", choices=_CHOICES, checks=["mise run test"], requires_checks={"pass"}
    )
    hub.apply_responses = [ApplyReply(outcome=ApplyOutcome.DONE)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100), verdict="pass"
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

    Advance(ctx).run()  # launches the detached elicitation
    Advance(ctx).run()  # collects it — the fake pid reads dead by default
    Pull(ctx).run()

    assert len(hub.completions) == 1
    assert hub.completions[0][1].choice == "pass"
