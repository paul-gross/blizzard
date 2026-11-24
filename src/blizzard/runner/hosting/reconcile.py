"""The host tick's decision over its hosted runners — which to start and which to drain — and the start failures it
retries."""

from __future__ import annotations

import threading
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.foundation.runner_start_step import RunnerStartStep


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


@domain_model
@dataclass(frozen=True)
class RunnerStartFailure:
    """Why one runner's latest start failed: the step it stopped at and the error."""

    step: RunnerStartStep
    error: str


class RunnerStartFailures:
    """Each runner whose latest start failed, by id, until it starts or is no longer to start."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: dict[str, RunnerStartFailure] = {}

    def hold(self, runner_id: str, failure: RunnerStartFailure) -> bool:
        """Hold ``failure`` as ``runner_id``'s; whether it differs from the one held."""
        with self._lock:
            changed = self._held.get(runner_id) != failure
            self._held[runner_id] = failure
            return changed

    def clear(self, runner_id: str) -> None:
        with self._lock:
            self._held.pop(runner_id, None)

    def of(self, runner_id: str) -> RunnerStartFailure | None:
        with self._lock:
            return self._held.get(runner_id)

    def failed(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._held)
