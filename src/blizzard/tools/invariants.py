"""The invariant checker's composition root (``bzh:invariant-checker``): wires the runner half
(``src/blizzard/runner/store/internal/invariants.py``) and the hub half
(``src/blizzard/hub/store/internal/invariants.py``) to their stores and runs both. After any crash → restart →
recover cycle, the durable facts in both stores must still satisfy the correctness conditions the design rests on."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.invariants import Violation
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import collaborator
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.composition import build_hub_invariants
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.store.internal.invariants import HubInvariants
from blizzard.runner.composition import build_host_stores
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from blizzard.runner.process.probe import IProcessProbe
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.store.internal.invariants import RunnerInvariants


def runner_invariants(engine: Engine, *, process: IProcessProbe | None = None) -> RunnerInvariants:
    """The runner half over ``engine``, reading through the host stores' runner audit; ``process`` defaults to the
    real ``/proc`` probe."""
    audit = build_host_stores(
        engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.tools.invariants")), clock=SystemClock()
    ).runner_audit
    return RunnerInvariants(audit, process if process is not None else LinuxProcessProbe())


def hub_invariants(engine: Engine, *, clock: IClock | None = None) -> HubInvariants:
    """The hub half over ``engine``, built as the hub's own composition builds it."""
    connections = HubStoreConnections(engine, HubStoreErrorFactory(get_logger("blizzard.hub.store")))
    return build_hub_invariants(connections, clock if clock is not None else SystemClock())


@collaborator
@dataclass(frozen=True)
class Invariants:
    """Both stores' durable invariants, over whichever URLs are given — each store is opened
    read-only over its own engine, and an empty result means every checked invariant holds."""

    runner_db_url: str | None = None
    hub_db_url: str | None = None

    def run(self, *, after_recovery: bool = False) -> list[Violation]:
        """``after_recovery`` rides straight through to :meth:`RunnerInvariants.run` —
        see its own docstring for why that one check needs it."""
        violations: list[Violation] = []
        if self.runner_db_url is not None:
            violations.extend(
                runner_invariants(create_engine_from_url(self.runner_db_url)).run(after_recovery=after_recovery)
            )
        if self.hub_db_url is not None:
            violations.extend(hub_invariants(create_engine_from_url(self.hub_db_url)).run())
        return violations
