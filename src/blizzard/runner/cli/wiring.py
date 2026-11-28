"""Worker command registrations over the runner group's borrowed session."""

from dataclasses import dataclass

from blizzard.cli.collaborators import ClientFactory
from blizzard.cli.operator_trace import ITraceHeaders
from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.runner.cli.worker_call import WorkerCall, WorkerPolicy, WorkerSession

SESSION = port(WorkerSession)
POLICY = port(WorkerPolicy)


@dataclass(frozen=True)
class WorkerHook:
    call: WorkerCall | None


@dataclass(frozen=True)
class WorkerOperation:
    session: WorkerSession
    policy: WorkerPolicy
    client_factory: ClientFactory
    trace_headers: ITraceHeaders

    def call(self) -> WorkerCall:
        worker = WorkerCall.select(
            self.session, self.policy, client_factory=self.client_factory, trace_headers=self.trace_headers
        )
        assert worker is not None
        return worker


def _worker(
    session: WorkerSession, policy: WorkerPolicy, client_factory: ClientFactory, trace_headers: ITraceHeaders
) -> WorkerCall:
    worker = WorkerCall.select(session, policy, client_factory=client_factory, trace_headers=trace_headers)
    assert worker is not None
    return worker


def _hook(
    session: WorkerSession, policy: WorkerPolicy, client_factory: ClientFactory, trace_headers: ITraceHeaders
) -> WorkerHook:
    return WorkerHook(WorkerCall.select(session, policy, client_factory=client_factory, trace_headers=trace_headers))


def _operation(
    session: WorkerSession, policy: WorkerPolicy, client_factory: ClientFactory, trace_headers: ITraceHeaders
) -> WorkerOperation:
    return WorkerOperation(session, policy, client_factory, trace_headers)


def register(registry: Registry) -> None:
    registry.context(SESSION, lifetime=Lifetime.COMMAND)
    registry.context(POLICY, lifetime=Lifetime.COMMAND)
    registry.factory(_worker, lifetime=Lifetime.COMMAND)
    registry.factory(_hook, lifetime=Lifetime.COMMAND)
    registry.factory(_operation, lifetime=Lifetime.COMMAND)
