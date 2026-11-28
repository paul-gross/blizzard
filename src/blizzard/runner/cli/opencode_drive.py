"""Worker turn driver for the operator's OpenCode 2 service."""

from __future__ import annotations

import os
import signal
import sys
import threading
from functools import partial
from pathlib import Path

import click

from blizzard.cli import command_root
from blizzard.cli.collaborators import CliCollaborators
from blizzard.foundation.clock import IMonotonicClock
from blizzard.foundation.registry.scopes import ScopeValues, supply
from blizzard.runner.cli import opencode_drive_wiring
from blizzard.runner.cli.children import RunnerCliCollaborators
from blizzard.runner.cli.container_command import ContainerWorkerCommand
from blizzard.runner.cli.worker import send_heartbeat
from blizzard.runner.cli.worker_call import WorkerPolicy, WorkerSession
from blizzard.runner.harness.opencode_v2 import wiring as service_wiring
from blizzard.runner.harness.opencode_v2.drive import TurnDriver
from blizzard.runner.harness.opencode_v2.driver import BIND_FAULTS, DriverCore
from blizzard.runner.harness.opencode_v2.errors import ErrorKind, TurnError
from blizzard.runner.harness.opencode_v2.registration import read_registration, registration_path
from blizzard.runner.harness.opencode_v2.request import DriveRequestError, take_request
from blizzard.runner.harness.opencode_v2.result import Outcome, ResultWriter
from blizzard.runner.harness.opencode_v2.transport import IServiceTransport


class DriveCommand(ContainerWorkerCommand):
    def values(self, ctx: click.Context) -> ScopeValues:
        carrier = RunnerCliCollaborators.provided(ctx)
        override = service_wiring.ServiceOverride(carrier.service if carrier else None)
        clock = carrier.monotonic_clock if carrier else CliCollaborators.of(ctx).monotonic_clock
        return ScopeValues(
            (
                *super().values(ctx).items,
                supply(service_wiring.SERVICE_OVERRIDE, override),
                supply(opencode_drive_wiring.CLOCK, clock),
            )
        )

    def areas(self) -> list[command_root.Area]:
        return [*super().areas(), opencode_drive_wiring.register, service_wiring.register]


@click.command(
    "drive",
    cls=DriveCommand,
    collaborators={"session": WorkerSession, "service": IServiceTransport, "clock": IMonotonicClock},
    policy=WorkerPolicy("opencode drive", traced=False),
)
@click.option(
    "--request",
    "request_path",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="The turn's request file, written by the runner; read once, then removed.",
)
def opencode_drive(
    request_path: Path, session: WorkerSession, service: IServiceTransport, clock: IMonotonicClock
) -> None:
    """Worker: drive one OpenCode 2 turn on the operator's managed service and print its result.

    The worker entrypoint the OpenCode 2 binding launches for each spawn, resume, judgement, and nudge, not meant to
    be run by hand; its exit ends the turn. SIGINT interrupts the session tree and ends the turn interrupted."""
    try:
        request = take_request(request_path)
    except DriveRequestError as exc:
        raise click.ClickException(str(exc)) from exc
    environment = dict(os.environ)
    path = registration_path(environment)
    try:
        core = DriverCore(lambda: read_registration(path) if path is not None else None, service, monotonic_clock=clock)
    except BIND_FAULTS as exc:
        error = TurnError(ErrorKind.SERVICE, f"no OpenCode service to drive the turn on: {exc}")
        ResultWriter(sys.stdout, request.session_id).finish(Outcome.FAILED, "", (), error)
        return
    stop = threading.Event()
    driver = TurnDriver(
        core,
        request,
        environment,
        sys.stdout,
        heartbeat=partial(send_heartbeat, session),
        stop=stop,
        monotonic_clock=clock,
    )

    def interrupt(signum: int, frame: object) -> None:
        del signum, frame
        stop.set()
        driver.wake.set()

    signal.signal(signal.SIGINT, interrupt)
    driver.run()
