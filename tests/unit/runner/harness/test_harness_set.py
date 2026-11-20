import weakref
from unittest.mock import Mock

import pytest

from blizzard.runner.harness.harness_set import (
    HarnessAssemblyKey,
    HarnessSet,
    HarnessSets,
    JudgeInvocation,
    WorkerInvocation,
)


def test_only_last_key_is_cached_and_failed_build_does_not_replace_it() -> None:
    first, second, third = Mock(), Mock(), Mock()
    build = Mock(side_effect=[first, second, RuntimeError("failed"), third])
    sets = HarnessSets(build)
    a = HarnessAssemblyKey(("settings",), ("binary-a",), "source-a")
    b = HarnessAssemblyKey(("settings",), ("binary-b",), "source-a")
    c = HarnessAssemblyKey(("settings",), ("binary-b",), "source-b")

    assert sets.select(a) is first
    assert sets.select(a) is first
    assert build.call_count == 1
    assert sets.select(b) is second
    with pytest.raises(RuntimeError, match="failed"):
        sets.select(c)
    assert sets.current is second
    assert sets.select(b) is second
    assert sets.select(a) is third
    assert build.call_count == 4


def test_retained_invocations_keep_their_original_set() -> None:
    first, second = Mock(), Mock()
    sets = HarnessSets(Mock(side_effect=[first, second]))
    sets.select(HarnessAssemblyKey((1,)))
    assert sets.retain("pending-process") is first
    assert sets.retain("other-process") is first
    sets.select(HarnessAssemblyKey((2,)))
    assert sets.lookup("pending-process") is first
    assert sets.retain("new-process") is second
    sets.release("pending-process")
    with pytest.raises(KeyError):
        sets.lookup("pending-process")
    assert sets.lookup("other-process") is first
    sets.release("other-process")
    assert sets.lookup("new-process") is second


def test_prepared_set_is_not_published_until_context_is_ready() -> None:
    first, second = Mock(), Mock()
    sets = HarnessSets(Mock(side_effect=[first, second]))
    sets.select(HarnessAssemblyKey((1,)))
    key = HarnessAssemblyKey((2,))
    candidate = sets.prepare(key)
    assert sets.current is first
    assert sets.retain("before") is first
    sets.publish(key, candidate)
    assert sets.retain("after") is second
    assert sets.lookup("before") is first


def test_provisional_ownership_moves_to_process_even_after_replacement() -> None:
    first, second = Mock(), Mock()
    sets = HarnessSets(Mock(side_effect=[first, second]))
    sets.select(HarnessAssemblyKey((1,)))
    provisional = object()
    sets.retain(provisional)
    sets.select(HarnessAssemblyKey((2,)))

    process = (42, "start-time")
    sets.transfer(provisional, process)
    assert sets.lookup(process) is first
    with pytest.raises(KeyError):
        sets.lookup(provisional)
    assert sets.current is second


def test_transfer_collision_preserves_both_owners() -> None:
    first, second = Mock(), Mock()
    sets = HarnessSets(Mock(side_effect=[first, second]))
    sets.select(HarnessAssemblyKey((1,)))
    sets.retain("pending")
    sets.select(HarnessAssemblyKey((2,)))
    sets.retain("process")
    with pytest.raises(ValueError, match="already retained"):
        sets.transfer("pending", "process")
    assert sets.lookup("pending") is first
    assert sets.lookup("process") is second


def test_replaced_set_lives_until_last_invocation_releases_it() -> None:
    first, second = Mock(), Mock()
    first_ref = weakref.ref(first)
    remaining = [second, first]
    sets = HarnessSets(lambda _: remaining.pop())
    sets.select(HarnessAssemblyKey((1,)))
    sets.retain("worker")
    sets.retain("judge")
    sets.select(HarnessAssemblyKey((2,)))
    del first
    sets.release("worker")
    assert first_ref() is not None
    sets.release("judge")
    assert first_ref() is None


def test_retained_invocations_keep_set_local_collaborators_across_replacement() -> None:
    old_registry, new_registry = Mock(), Mock()
    old_bundle, new_bundle = Mock(), Mock()
    old = Mock(spec=HarnessSet, registry=old_registry, bundle=old_bundle)
    new = Mock(spec=HarnessSet, registry=new_registry, bundle=new_bundle)
    sets = HarnessSets(Mock(side_effect=[old, new]))
    sets.select(HarnessAssemblyKey((1,)))

    # Session identity is not the invocation identity: these two invocations can
    # resume the same session while still requiring independent ownership.
    worker, judge = object(), object()
    sets.retain(worker)
    sets.retain(judge)
    sets.select(HarnessAssemblyKey((2,)))
    resumed = object()
    sets.retain(resumed)

    assert sets.lookup(worker).registry is old_registry
    assert sets.lookup(judge).bundle is old_bundle
    assert sets.lookup(resumed).registry is new_registry
    assert sets.lookup(resumed).bundle is new_bundle
    sets.release(worker)
    assert sets.lookup(judge).registry is old_registry
    sets.release(judge)
    with pytest.raises(KeyError):
        sets.lookup(judge)


def test_relaunched_judges_keep_prior_owners_until_cohort_terminal_work() -> None:
    old, new = Mock(), Mock()
    old_ref = weakref.ref(old)
    remaining = [new, old]
    sets = HarnessSets(lambda _: remaining.pop())
    sets.select(HarnessAssemblyKey((1,)))
    active = JudgeInvocation("lease", 1)
    sets.retain(active)
    sets.select(HarnessAssemblyKey((2,)))
    sets.retire_judge("lease", 1)
    sets.retain(active)
    sets.retire_judge("lease", 1)
    sets.retain(active)
    del old

    assert old_ref() is not None
    assert sets.lookup(active) is new
    sets.release_judges("lease", 1)
    assert old_ref() is None
    assert sets.find(active) is None


def test_worker_lookup_uses_latest_retained_generation_per_lease() -> None:
    first, second = Mock(), Mock()
    sets = HarnessSets(Mock(side_effect=[first, second]))
    sets.select(HarnessAssemblyKey((1,)))
    sets.retain(WorkerInvocation("a", 1))
    sets.retain(WorkerInvocation("b", 1))
    sets.select(HarnessAssemblyKey((2,)))
    sets.retain(WorkerInvocation("a", 2))
    assert sets.worker_for_lease("a") is second
    assert sets.worker_for_lease("b") is first
    sets.release(WorkerInvocation("a", 2))
    assert sets.worker_for_lease("a") is first
    sets.release_lease("a")
    assert sets.worker_for_lease("a") is None


def _replaced_with_retained() -> tuple[HarnessSets, Mock, Mock, Mock]:
    """Worker gen 1 on the first set, its judge on the second, gen 2 worker on the second, current the third."""
    first, second, third = Mock(), Mock(), Mock()
    sets = HarnessSets(Mock(side_effect=[first, second, third]))
    sets.select(HarnessAssemblyKey((1,)))
    sets.retain(WorkerInvocation("lease", 1))
    sets.select(HarnessAssemblyKey((2,)))
    sets.retain(JudgeInvocation("lease", 1))
    sets.retain(WorkerInvocation("lease", 2))
    sets.select(HarnessAssemblyKey((3,)))
    return sets, first, second, third


@pytest.mark.parametrize(
    ("lease_id", "generation", "judge", "expected"),
    [
        pytest.param("lease", 1, False, "first", id="exact-worker"),
        pytest.param("lease", 1, True, "second", id="exact-judge"),
        pytest.param("lease", 2, True, "second", id="judge-falls-back-to-same-generation-worker"),
        pytest.param("lease", None, False, "second", id="lease-only-newest-worker"),
        pytest.param("lease", None, True, "second", id="lease-only-ignores-judge"),
        pytest.param("lease", 3, False, "third", id="worker-falls-back-to-current"),
        pytest.param("lease", 3, True, "third", id="judge-falls-back-to-current"),
        pytest.param("other", None, False, "third", id="lease-only-falls-back-to-current"),
    ],
)
def test_owner_resolves_the_retained_set_else_current(
    lease_id: str, generation: int | None, judge: bool, expected: str
) -> None:
    sets, first, second, third = _replaced_with_retained()
    by_name = {"first": first, "second": second, "third": third}

    assert sets.owner(lease_id, generation, judge=judge) is by_name[expected]
