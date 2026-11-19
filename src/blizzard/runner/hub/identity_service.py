"""The runner identity row's forgetting — what a runner's retirement forgets.

``runner retire`` on a stopped host, and the running host's tick, forget the row of a runner the hub retired; a
joined runner's row is written with its hosting, by the runner admission. The edge and the tick decide nothing about
the row: they hand this service the fact, and it writes."""

from __future__ import annotations

from blizzard.foundation.logging import get_logger
from blizzard.runner.hub.client import HubClientError, ITokenIdentityReader
from blizzard.runner.hub.identity import (
    IReadRunnerIdentityRepository,
    IWriteRetiredRunnerIdentity,
    proves_retired,
)

_log = get_logger("blizzard.runner.host")


class RunnerIdentityService:
    """Forgets a retired runner's identity row, over the store bound to that runner."""

    def __init__(self, *, forgetter: IWriteRetiredRunnerIdentity):
        self._forgetter = forgetter

    def forget(self) -> bool:
        """Forget the bound runner's row if it has no retained settings or history."""
        return self._forgetter.forget_runner_identity()


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
        if not self._forgetter.forget_runner_identity():
            return False
        _log.info("forgot the identity row of a runner the hub retired", runner_id=self._runner_id)
        return True
