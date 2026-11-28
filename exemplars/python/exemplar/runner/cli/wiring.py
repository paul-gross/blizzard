"""Registrations for a worker's lease-history read."""

# exemplar: bzh:dependency-injection — each area registers the collaborators its operation consumes.

from __future__ import annotations

import click

from blizzard.foundation.registry.interface import Lifetime, Registry
from blizzard.runner.cli.worker_call import WorkerCall


class HistoryReader:
    def __init__(self, worker: WorkerCall) -> None:
        self._worker = worker

    def show(self) -> None:
        answer = self._worker.get(self._worker.leased("history"), failure="could not read the history")
        click.echo(answer.text)


def register(registry: Registry) -> None:
    registry.service(HistoryReader, lifetime=Lifetime.COMMAND)
