"""``Judgement.checks``'s idempotency and cwd resolution, the check-result store round trip, and the pure rules that
render and gate on a node's ``checks:`` results."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from blizzard.foundation.clock import FixedClock
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.leases.model import NewLease
from blizzard.runner.lifecycle.judgement.check_runner import DEFAULT_CHECK_TIMEOUT, CheckOutcome
from blizzard.runner.lifecycle.judgement.checks import ExecutedCheck
from blizzard.runner.lifecycle.judgement.judgement import Judgement
from blizzard.runner.lifecycle.judgement.judgement_prompt import JudgementPrompt
from tests.support.runner_fakes import (
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
from tests.support.runner_mint import new_mint

_NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


def _prompt(results: list[ExecutedCheck]) -> JudgementPrompt:
    return JudgementPrompt(make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES), results)


def _seed_exited_lease(
    store: SqlAlchemyRunnerStore, *, lease_id: str, chunk_id: str, node_id: str, epoch: int, workdir: str = "/ws/e1"
) -> None:
    store.record_mint(
        new_mint(
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
    )
    store.record_spawn(
        lease_id,
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id=chunk_id, environment_id="e1", workdir=workdir, bound_at=_NOW)


# --- Unit: the check step in isolation, faked seam -----------------------------


def _ctx_for_unit(store: SqlAlchemyRunnerStore, *, check_runner: FakeCheckRunner, clock: FixedClock):
    return make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=WorkerHandle(session_id="s", pid=1, process_start_time="t", pgid=1), verdict="pass"),
        probe=FakeProbe(),
        check_runner=check_runner,
        clock=clock,
    )


def _lease_and_bindings(
    store: SqlAlchemyRunnerStore, *, checks_cwd: str | None = None, checks_timeout: int | None = None
):
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


def test_checks_resolves_checks_cwd_relative_to_the_binding_workdir(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    check_runner = FakeCheckRunner()
    ctx = _ctx_for_unit(store, check_runner=check_runner, clock=FixedClock(_NOW))
    lease, bindings, envelope = _lease_and_bindings(store, checks_cwd="blizzard", checks_timeout=42)

    Judgement(ctx, lease, envelope, bindings).checks()

    assert check_runner.calls == [("mise run lint", "/ws/e1/blizzard", 42)]


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


def test_record_check_results_round_trips_and_is_latest_wins_on_a_rerun(tmp_path: Path) -> None:
    """A recovery re-run (same ``(lease, epoch)``) replaces the prior rows rather than
    accumulating duplicates — ``check_results_for_lease`` reads exactly one run's rows."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    store.record_check_results(
        lease_id="lease_b",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        results=[ExecutedCheck(command="c1", passed=False, output_tail="red")],
        at=_NOW,
    )
    assert [(r.command, r.passed) for r in store.check_results_for_lease("lease_b", 1)] == [("c1", False)]

    # Re-run (a crash after rows, before marker → recovery re-runs) — latest-wins overwrite.
    store.record_check_results(
        lease_id="lease_b",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        results=[ExecutedCheck(command="c1", passed=True, output_tail="green")],
        at=_NOW,
    )
    assert [(r.command, r.passed, r.output_tail) for r in store.check_results_for_lease("lease_b", 1)] == [
        ("c1", True, "green")
    ]


def test_checks_ran_marker_is_idempotent(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    assert store.checks_ran("lease_b", 1) is False
    store.record_checks_ran(lease_id="lease_b", epoch=1, at=_NOW)
    store.record_checks_ran(lease_id="lease_b", epoch=1, at=_NOW)  # a genuine replay
    assert store.checks_ran("lease_b", 1) is True


# --- injection of check results into the judgement prompt -------------


def test_checks_block_renders_pass_and_fail_and_shows_the_tail_only_on_failure() -> None:
    block = _prompt(
        [
            ExecutedCheck(command="mise run lint", passed=True, output_tail="all good"),
            ExecutedCheck(command="mise run test", passed=False, output_tail="assert 1 == 2\n1 failed"),
        ]
    ).checks_block()
    # Harness-inert: every line is `#`-prefixed so a mock harness that execs the prompt
    # sees only comments.
    assert all(line.startswith("#") for line in block.splitlines() if line)
    assert "[PASS] mise run lint" in block
    assert "[FAIL] mise run test" in block
    # The failing check's tail is shown (the worker needs the why); the passing one's is not.
    assert "assert 1 == 2" in block
    assert "1 failed" in block
    assert "all good" not in block


def test_checks_block_is_empty_for_no_checks() -> None:
    assert _prompt([]).checks_block() == ""


# --- the checks gate (runner-local) -----------------------------------


def test_checks_gate_violated_predicate() -> None:
    from blizzard.foundation.completion_gates import ChecksGate
    from blizzard.runner.node_steps.submissions import CheckVerdict

    green = [CheckVerdict(command="a", passed=True), CheckVerdict(command="b", passed=True)]
    red = [CheckVerdict(command="a", passed=True), CheckVerdict(command="b", passed=False)]
    # Ungated: never violated, whatever the checks say.
    assert ChecksGate(False, red).violated is False
    assert ChecksGate(False, green).violated is False
    # Gated: violated iff any check is red.
    assert ChecksGate(True, green).violated is False
    assert ChecksGate(True, red).violated is True
    # Gated with no results is vacuously satisfied (the validator forbids this shape anyway).
    assert ChecksGate(True, []).violated is False


def test_the_default_check_timeout_is_ten_minutes() -> None:
    # Pinned to the literal, not the constant: every other test compares against the
    # constant, so a changed default would pass them all.
    assert DEFAULT_CHECK_TIMEOUT == 600
