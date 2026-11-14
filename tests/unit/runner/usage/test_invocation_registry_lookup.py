"""Usage resolves the individual invocation's retained registry, including judge owners."""

from unittest.mock import Mock

from blizzard.runner.harness.harness_set import (
    HarnessAssemblyKey,
    HarnessSet,
    HarnessSets,
    JudgeInvocation,
    WorkerInvocation,
)
from blizzard.runner.harness.registry import HarnessRegistry
from blizzard.runner.loop_wiring import UsageRegistryLookup


def test_usage_lookup_uses_retained_worker_and_judge_then_current() -> None:
    old = HarnessRegistry({})
    current = HarnessRegistry({})
    sets = HarnessSets(Mock(side_effect=[Mock(spec=HarnessSet, registry=old), Mock(spec=HarnessSet, registry=current)]))
    sets.select(HarnessAssemblyKey((1,)))
    sets.retain(WorkerInvocation("lease", 1))
    sets.retain(JudgeInvocation("lease", 1))
    sets.select(HarnessAssemblyKey((2,)))
    lookup = UsageRegistryLookup(sets, current)

    assert lookup("lease", 1, False) is old
    assert lookup("lease", 1, True) is old
    assert lookup("lease", 2, False) is current
    assert UsageRegistryLookup(None, old)("lease", 1, False) is old
