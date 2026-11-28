"""Worker command registrations over the runner group's borrowed session."""

from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.runner.cli.worker_call import WorkerPolicy, WorkerSession
from blizzard.runner.cli.worker_dependencies import hook, operation, worker

SESSION = port(WorkerSession)
POLICY = port(WorkerPolicy)


def register(registry: Registry) -> None:
    registry.context(SESSION, lifetime=Lifetime.COMMAND)
    registry.context(POLICY, lifetime=Lifetime.COMMAND)
    registry.factory(worker, lifetime=Lifetime.COMMAND)
    registry.factory(hook, lifetime=Lifetime.COMMAND)
    registry.factory(operation, lifetime=Lifetime.COMMAND)
