"""``blizzard hub runner`` — issues #104/#86a: operator verbs over one runner."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import click

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing, RunnerRow


class RunnerListing(Listing):
    empty = "no runners registered"

    def line(self, row: Any) -> str:
        return RunnerRow(row).line()


@dataclass(frozen=True)
class RunnerDetail:
    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        yield f"{self.body['runner_id']}  {RunnerRow(self.body).liveness}  ws={self.body.get('workspace_id', '-')}"
        yield f"  hub_paused={self.body.get('hub_paused')}  locally_paused={self.body.get('locally_paused')}"


@click.group("runner")
def runner_group() -> None:
    """Operator verbs over one runner: identity, liveness, and its pause brake."""


@runner_group.command("list", cls=FleetCommand)
def runner_list(cli: CliContext) -> None:
    """The fleet registry — every runner with derived liveness + paused state."""
    body = cli.get("/api/runners", "GET /runners").json()
    cli.show(body, RunnerListing(body.get("runners", [])))


@runner_group.command("show", cls=FleetCommand)
@click.argument("runner_id")
def runner_show(cli: CliContext, runner_id: str) -> None:
    """One runner's derived liveness + paused state, symmetric with ``runner list``."""
    resp = cli.get(f"/api/runners/{runner_id}", "GET /runners/{id}", on_status={404: f"unknown runner {runner_id}"})
    body = resp.json()
    cli.show(body, RunnerDetail(body))


@runner_group.command("pause", cls=FleetCommand)
@click.argument("runner_id")
@click.option("--by", "by", default="operator", help="Who is pausing (recorded on the fact).")
def runner_pause(cli: CliContext, runner_id: str, by: str) -> None:
    """Pause a runner — it stops claiming new work; in-flight chunks run on."""
    _set_runner_pause(cli, runner_id, verb="pause", by=by)


@runner_group.command("resume", cls=FleetCommand)
@click.argument("runner_id")
@click.option("--by", "by", default="operator", help="Who is resuming (recorded on the fact).")
def runner_resume(cli: CliContext, runner_id: str, by: str) -> None:
    """Resume a paused runner — it claims work again on its next pull."""
    _set_runner_pause(cli, runner_id, verb="resume", by=by)


def _set_runner_pause(cli: CliContext, runner_id: str, *, verb: str, by: str) -> None:
    resp = cli.post(
        f"/api/runners/{runner_id}/{verb}",
        f"POST /runners/{{id}}/{verb}",
        json_body={"by": by},
        on_status={404: f"unknown runner {runner_id}"},
    )
    body = resp.json()
    state = "paused" if body.get("hub_paused") else "running"
    lines = [f"runner {runner_id} is now {state} (at the hub)"]
    if body.get("locally_paused"):
        # Resuming here cannot clear the runner's own brake, so don't imply it did.
        lines.append(f"note: runner {runner_id} also paused itself — clear that with `blizzard runner start`")
    cli.show_lines(body, *lines)


@runner_group.command("enroll", cls=FleetCommand)
@click.argument("runner_id")
def runner_enroll(cli: CliContext, runner_id: str) -> None:
    """Mint (or rotate) RUNNER_ID's bearer token; prints the plaintext exactly once.

    A thin client of ``POST /runners/{id}/enrollments`` (issue #86a). Re-running
    rotates: the old token stops resolving immediately. RUNNER_ID must already be
    registered at the hub (404 otherwise)."""
    resp = cli.post(
        f"/api/runners/{runner_id}/enrollments",
        "POST /runners/{id}/enrollments",
        on_status={404: f"unknown runner {runner_id}"},
    )
    body = resp.json()
    cli.show_lines(body, f"enrolled {runner_id} — bearer token (copy now, shown only once):\n{body['token']}")
