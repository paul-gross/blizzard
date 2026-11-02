"""The runner identity row's writers — what a runner's join records and what its retirement forgets.

``runner init`` records the row of a runner it joined; ``runner retire`` on a stopped host, and the running host's
tick, forget the row of a runner the hub retired. The edge and the tick decide nothing about the row: they hand
this service the fact, and it writes."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.runner.hub.bootstrap import JoinedRunner
from blizzard.runner.hub.client import HubClientError, ITokenIdentityReader
from blizzard.runner.hub.identity import (
    IReadRunnerIdentityRepository,
    IWriteRetiredRunnerIdentity,
    IWriteRunnerIdentityRepository,
    proves_retired,
)

_log = get_logger("blizzard.runner.host")


class RunnerIdentityService:
    """Records a joined runner's identity row and forgets a retired runner's, over the store bound to that runner."""

    def __init__(
        self, *, recorder: IWriteRunnerIdentityRepository, forgetter: IWriteRetiredRunnerIdentity, clock: IClock
    ):
        self._recorder = recorder
        self._forgetter = forgetter
        self._clock = clock

    def record(self, joined: JoinedRunner) -> None:
        """Write the row of ``joined`` from the hub's answer, so a configured runner has its row before it writes
        anything else."""
        self._recorder.record_runner_identity(joined.identity_at(self._clock.now()))

    def forget(self) -> None:
        """Delete the bound runner's row — one transaction, idempotent."""
        self._forgetter.forget_runner_identity()


class RetiredRunnerWatch:
    """Forgets the row of the runner it is bound to once the hub proves the runner retired, asking the hub under the
    runner's own token."""

    def __init__(
        self,
        *,
        runner_id: str,
        rows: IReadRunnerIdentityRepository,
        forgetter: IWriteRetiredRunnerIdentity,
        token_identity: ITokenIdentityReader,
    ) -> None:
        self._runner_id = runner_id
        self._rows = rows
        self._forgetter = forgetter
        self._token_identity = token_identity

    def forget_if_retired(self) -> bool:
        """Whether the row was forgotten. A runner with no row costs no hub call; an unreachable hub, or any answer
        short of ``retired`` naming this runner, leaves the row for the next tick."""
        if self._rows.runner_identity() is None:
            return False
        try:
            answer = self._token_identity.identity()
        except HubClientError:
            return False
        if not proves_retired(answer, self._runner_id):
            return False
        self._forgetter.forget_runner_identity()
        _log.info("forgot the identity row of a runner the hub retired", runner_id=self._runner_id)
        return True
