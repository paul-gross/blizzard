"""The host tick's decision over its hosted runners: which to start and which to drain."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class ReconcilePlan:
    """What one reconcile does: the runners to start, in hosting order, the running ones to drain, and the held start
    failures of runners no longer to start, which are dropped."""

    start: tuple[str, ...] = ()
    drain: tuple[str, ...] = ()
    forget_failures: tuple[str, ...] = ()

    def empty(self) -> bool:
        return not (self.start or self.drain or self.forget_failures)


def reconcile(
    active: Sequence[str], retired: Collection[str], running: Collection[str], failed: Collection[str]
) -> ReconcilePlan:
    """Start every ``active`` runner not ``running`` — a ``failed`` start among them, so it is retried — and drain every
    ``retired`` one still ``running``. A runner both active and retired is retired: retirement is final."""
    start = tuple(runner_id for runner_id in active if runner_id not in running and runner_id not in retired)
    drain = tuple(sorted(runner_id for runner_id in running if runner_id in retired))
    forget = tuple(sorted(runner_id for runner_id in failed if runner_id not in start))
    return ReconcilePlan(start=start, drain=drain, forget_failures=forget)
