"""Stopping the turn of a worker the runner killed: a killed driver leaves its turn running on the operator's service,
so its running session tree is interrupted through the API — best effort, the whole call held to one bound, and never
a service started to do it. A tree running nothing is left alone; a failure is logged and left to reconciliation."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.opencode_v2.driver import BIND_FAULTS, DriverCore, RegistrationSource
from blizzard.runner.harness.opencode_v2.transport import BoundedTransport, IServiceTransport

_log = get_logger("blizzard.runner.harness")

DEFAULT_ORPHAN_BOUND_SECONDS = 5.0


class ISessionOrphaner(Protocol):
    def orphan(self, session_id: str) -> bool:
        """Interrupt ``session_id``'s running tree, best effort and bounded, and return whether it interrupted one;
        never raises."""
        ...


class SessionOrphaner:
    def __init__(
        self,
        source: RegistrationSource,
        transport: IServiceTransport,
        *,
        bound_seconds: float = DEFAULT_ORPHAN_BOUND_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._source = source
        self._transport = transport
        self._bound_seconds = bound_seconds
        self._monotonic = monotonic

    def orphan(self, session_id: str) -> bool:
        """Interrupt ``session_id``'s running descendants, deepest first, then the session, when its tree runs; whether
        it did."""
        transport = BoundedTransport(self._transport, self._monotonic() + self._bound_seconds, self._monotonic)
        try:
            core = DriverCore(self._source, transport, poll_seconds=0.0)
            state = core.tree_state(session_id)
            if not state.running:
                _log.debug("an orphaned OpenCode 2 session runs nothing", session_id=session_id)
                return False
            running = set(state.running)
            stopped = [sid for sid in reversed(state.session_ids[1:]) if sid in running]
            for sid in (*stopped, session_id):
                core.client.interrupt(sid)
        except (*BIND_FAULTS, OSError) as exc:
            _log.warning("could not interrupt an orphaned OpenCode 2 session", session_id=session_id, detail=str(exc))
            return False
        _log.info("interrupted an orphaned OpenCode 2 session", session_id=session_id, interrupted=len(stopped) + 1)
        return True


__all__ = ["DEFAULT_ORPHAN_BOUND_SECONDS", "ISessionOrphaner", "SessionOrphaner"]
