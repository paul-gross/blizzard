"""Keeping the operator's managed service up: checked before every launch and started with ``opencode service start``
under the runner's tool profile when none answers. The runner never stops or restarts it."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from blizzard.runner.harness.opencode_v2.driver import (
    BIND_FAULTS,
    ServiceIdentity,
    bind_service,
)
from blizzard.runner.harness.opencode_v2.registration import read_registration, registration_path
from blizzard.runner.harness.opencode_v2.transport import (
    IServiceTransport,
)
from blizzard.runner.process.child_env import AllowlistedEnv
from blizzard.runner.process.child_processes import ChildLaunchError, IChildProcesses

DEFAULT_START_TIMEOUT_SECONDS = 30.0
DEFAULT_REACH_SECONDS = 30.0


class ServiceUnavailableError(RuntimeError):
    """No managed service answers, and starting one did not make one answer within the bound."""


class IServiceKeeper(Protocol):
    def answering(self) -> ServiceIdentity | None:
        """The answering service's identity, or ``None`` when none answers; never starts one."""
        ...

    def ensure(self) -> ServiceIdentity:
        """The answering service's identity, starting one first when none answers; raises
        :class:`ServiceUnavailableError` when none answers within the bound."""
        ...


class ServiceKeeper:
    def __init__(
        self,
        binary: str,
        worker_env: AllowlistedEnv,
        children: IChildProcesses,
        transport: IServiceTransport,
        *,
        reach_seconds: float = DEFAULT_REACH_SECONDS,
        poll_seconds: float = 0.2,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._binary = binary
        self._tool_env = worker_env.tool()
        self._children = children
        self._transport = transport
        self._reach_seconds = reach_seconds
        self._poll_seconds = poll_seconds
        self._monotonic = monotonic
        self._sleep = sleep

    def _registration_file(self) -> Path:
        path = registration_path(self._tool_env.variables)
        if path is None:
            raise ServiceUnavailableError("neither XDG_STATE_HOME nor HOME locates the OpenCode service registration")
        return path

    def answering(self) -> ServiceIdentity | None:
        path = self._registration_file()
        try:
            _, identity = bind_service(lambda: read_registration(path), self._transport)
        except BIND_FAULTS:
            return None
        return identity

    def ensure(self) -> ServiceIdentity:
        """The answering service's identity, starting one first when none answers."""
        identity = self.answering()
        if identity is not None:
            return identity
        try:
            # `service start` detaches the service itself; the run's own group keeps any lease's kill off it.
            self._children.run(
                [self._binary, "service", "start"],
                cwd=None,
                env=self._tool_env,
                timeout=DEFAULT_START_TIMEOUT_SECONDS,
            )
        except ChildLaunchError as exc:
            raise ServiceUnavailableError(f"cannot run `{self._binary} service start`: {exc}") from exc
        deadline = self._monotonic() + self._reach_seconds
        while True:
            identity = self.answering()
            if identity is not None:
                return identity
            if self._monotonic() >= deadline:
                raise ServiceUnavailableError(
                    f"no OpenCode service answered within {self._reach_seconds:.0f}s of `service start`"
                )
            self._sleep(self._poll_seconds)


__all__ = ["DEFAULT_REACH_SECONDS", "IServiceKeeper", "ServiceKeeper", "ServiceUnavailableError"]
