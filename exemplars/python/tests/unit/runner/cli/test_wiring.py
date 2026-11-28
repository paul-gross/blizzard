"""Worker history registrations resolve over the group's borrowed session."""

# exemplar: bzh:dependency-injection — an area composes with the worker command's shared registrations.

from __future__ import annotations

from blizzard.cli import wiring as shared
from blizzard.cli.collaborators import CliCollaborators
from blizzard.cli.command_root import command_scope
from blizzard.foundation.registry.scopes import ScopeValues, supply
from blizzard.runner.cli import wiring as worker_wiring
from blizzard.runner.cli.worker_call import WorkerPolicy, WorkerSession
from exemplar.runner.cli.wiring import HistoryReader, register


def test_area_resolves_its_reader_over_a_borrowed_session() -> None:
    carrier = CliCollaborators.production()
    session = WorkerSession(carrier, {"BLIZZARD_RUNNER_URL": "http://runner", "BLIZZARD_LEASE_ID": "ls_1"})
    values = ScopeValues(
        (
            supply(shared.CLIENT_FACTORY, carrier.client_factory),
            supply(shared.TRACE_HEADERS, session),
            supply(worker_wiring.SESSION, session),
            supply(worker_wiring.POLICY, WorkerPolicy("history")),
        )
    )
    with command_scope(
        [shared.register, worker_wiring.register, register],
        owner="history",
        values=values,
        collaborators={"reader": HistoryReader},
    ) as resolved:
        assert isinstance(resolved["reader"], HistoryReader)
    assert session.environ["BLIZZARD_LEASE_ID"] == "ls_1"
