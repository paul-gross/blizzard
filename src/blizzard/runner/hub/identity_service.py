"""The running host's notice of a runner its hub retired.

The host tick hands the watch each runner its hub does not hold; on the hub's proof of retirement the watch records the
runner's ``retired`` fact, which the tick's reconcile drains. The runner's identity row stays."""

from __future__ import annotations

from blizzard.foundation.logging import get_logger
from blizzard.runner.hub.client import HubClientError, ITokenIdentityReader
from blizzard.runner.hub.identity import IReadRunnerIdentityRepository, IRecordRunnerRetired, proves_retired

_log = get_logger("blizzard.runner.host")


class RetiredRunnerWatch:
    """Records the retirement of the runner it is bound to once the hub proves the runner retired, asking the hub under
    the runner's own token."""

    def __init__(
        self,
        *,
        runner_id: str,
        rows: IReadRunnerIdentityRepository,
        retirement: IRecordRunnerRetired,
        token_identity: ITokenIdentityReader,
    ) -> None:
        self._runner_id = runner_id
        self._rows = rows
        self._retirement = retirement
        self._token_identity = token_identity

    def record_if_retired(self) -> bool:
        """Whether the runner's ``retired`` fact was written. A runner with no identity row costs no hub call; an
        unreachable hub, or any answer short of ``retired`` naming this runner, leaves it for the next tick."""
        if self._rows.runner_identity() is None:
            return False
        try:
            answer = self._token_identity.identity()
        except HubClientError:
            return False
        if not proves_retired(answer, self._runner_id):
            return False
        if not self._retirement.record_retired():
            return False
        _log.info("recorded the retirement of a runner the hub retired", runner_id=self._runner_id)
        return True
