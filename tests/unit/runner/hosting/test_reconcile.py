"""The host tick's reconcile decision (unit tier), by value."""

from __future__ import annotations

from blizzard.runner.hosting.reconcile import ReconcilePlan, reconcile


def test_an_active_runner_not_running_is_started_in_hosting_order() -> None:
    plan = reconcile(("rn_b", "rn_a", "rn_c"), (), running={"rn_a"}, failed=())

    assert plan == ReconcilePlan(start=("rn_b", "rn_c"))


def test_a_failed_start_is_retried_and_its_failure_kept() -> None:
    plan = reconcile(("rn_a", "rn_b"), (), running={"rn_a"}, failed={"rn_b"})

    assert plan == ReconcilePlan(start=("rn_b",))


def test_a_retired_runner_still_running_is_drained() -> None:
    plan = reconcile(("rn_a",), {"rn_b"}, running={"rn_a", "rn_b"}, failed=())

    assert plan == ReconcilePlan(drain=("rn_b",))


def test_a_retired_runner_not_running_is_neither_started_nor_drained() -> None:
    assert reconcile((), {"rn_b"}, running=(), failed=()).empty()


def test_a_runner_both_active_and_retired_is_retired() -> None:
    plan = reconcile(("rn_a",), {"rn_a"}, running={"rn_a"}, failed=())

    assert plan == ReconcilePlan(drain=("rn_a",))


def test_the_failure_of_a_runner_no_longer_to_start_is_forgotten() -> None:
    plan = reconcile(("rn_a",), {"rn_b"}, running={"rn_a"}, failed={"rn_b", "rn_c"})

    assert plan == ReconcilePlan(forget_failures=("rn_b", "rn_c"))


def test_a_running_runner_neither_retired_nor_hosted_is_left_running() -> None:
    assert reconcile((), (), running={"rn_a"}, failed=()).empty()
