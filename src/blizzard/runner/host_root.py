"""The runner host's composition root: it registers the host's keys, then opens the validated container over them.

``HostRunnerSet`` is the one process-lifetime service; each runner it hosts opens a child scope beneath the process
scope. The process scope borrows the :class:`~blizzard.runner.composition.HostProcess` it opens with, whose supplier
closes it. Registering and building are separate steps, so a caller declares its runner graph before the build."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.foundation.registry.internal.recording import RecordingRegistry
from blizzard.foundation.registry.internal.translation import HOST_LIFETIMES, build_container
from blizzard.foundation.registry.scopes import OwnedChild, ScopeValues, supply
from blizzard.foundation.roles import collaborator
from blizzard.runner.composition import HostProcess, HostRunnerSet, IRunnerResume, IWiredRunner
from blizzard.runner.host_config import BuiltRunner

#: The process scope's owner id.
HOST_OWNER = "host"


def register_host(registry: Registry) -> None:
    """Declare the borrowed host, the runner set over it, and the one value a runner child is opened with."""
    registry.context(port(HostProcess), lifetime=Lifetime.PROCESS)
    registry.service(HostRunnerSet, lifetime=Lifetime.PROCESS)
    registry.context(port(BuiltRunner), lifetime=Lifetime.RUNNER)


@collaborator
@dataclass(frozen=True)
class HostRoot[W: IWiredRunner, R: IRunnerResume]:
    """The host's open process scope and the runner set that owns the runner scopes beneath it."""

    owner: OwnedChild
    runners: HostRunnerSet[W, R]

    @classmethod
    def open(cls, registry: RecordingRegistry, host: HostProcess, *, eager: Sequence[type[Any]] = ()) -> HostRoot[W, R]:
        """Open the validated graph ``registry`` declares over the borrowed ``host``, and bind the runner set to open
        children beneath it; ``eager`` are the runner types built when a child opens. ``registry`` already holds
        :func:`register_host`'s keys."""
        values = ScopeValues((supply(port(HostProcess), host),))
        owner = build_container(registry, HOST_LIFETIMES, owner=HOST_OWNER, values=values)
        try:
            runners = cast("HostRunnerSet[W, R]", owner.resolver.get(HostRunnerSet))
            runners.bind(owner, eager=eager)
        except BaseException:
            owner.close()
            raise
        return cls(owner, runners)

    def close(self) -> None:
        """Close every runner the set holds, then the process scope, attempting the second when the first fails."""
        try:
            self.runners.close_all()
        finally:
            self.owner.close()
