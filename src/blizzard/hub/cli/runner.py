"""``blizzard hub runner`` — operator verbs over one runner."""

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
        if self.body.get("retired"):
            yield f"  retired={self.body.get('retired_at')} by {self.body.get('retired_by')}"


@click.group("runner")
def runner_group() -> None:
    """Operator verbs over one runner: identity, liveness, its pause brake, and retirement."""


@runner_group.command("list", cls=FleetCommand)
@click.option("--all", "include_retired", is_flag=True, default=False, help="Include retired runners, marked.")
def runner_list(cli: CliContext, include_retired: bool) -> None:
    """The fleet registry — every runner with derived liveness + paused state.

    Excludes a retired runner unless --all."""
    params = {"include_retired": "true"} if include_retired else None
    body = cli.get("/api/runners", "GET /runners", params=params).json()
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

    A thin client of ``POST /runners/{id}/enrollments``. Re-running
    rotates: the old token stops resolving immediately. RUNNER_ID must already be
    registered at the hub (404 otherwise) and not retired (409 — `reinstate` it first)."""
    resp = cli.post(
        f"/api/runners/{runner_id}/enrollments",
        "POST /runners/{id}/enrollments",
        on_status={404: f"unknown runner {runner_id}", 409: f"runner {runner_id} is retired"},
    )
    body = resp.json()
    cli.show_lines(body, f"enrolled {runner_id} — bearer token (copy now, shown only once):\n{body['token']}")


@runner_group.command("retire", cls=FleetCommand)
@click.argument("runner_id")
@click.option("--force", is_flag=True, default=False, help="Release every chunk the runner still holds.")
@click.option("--by", "by", default="operator", help="Who is retiring (recorded on the fact).")
def runner_retire(cli: CliContext, runner_id: str, force: bool, by: str) -> None:
    """Retire a runner — revoke its token and refuse its claims and registrations.

    Refused (409) while it holds chunks unless --force, which releases them. Re-running
    finishes a partial release. Stop the runner process too: the hub refuses it from here on."""
    resp = cli.post(
        f"/api/runners/{runner_id}/retire",
        "POST /runners/{id}/retire",
        json_body={"by": by, "force": force},
        on_status={404: f"unknown runner {runner_id}", 409: f"runner {runner_id} holds chunks"},
    )
    body = resp.json()
    lines = [f"runner {runner_id} is retired"]
    released = body.get("released_chunk_ids") or []
    if released:
        lines.append(f"released: {', '.join(released)}")
    cli.show_lines(body, *lines)


@runner_group.command("reinstate", cls=FleetCommand)
@click.argument("runner_id")
@click.option("--by", "by", default="operator", help="Who is reinstating (recorded on the fact).")
def runner_reinstate(cli: CliContext, runner_id: str, by: str) -> None:
    """Reinstate a retired runner — it stays unenrolled; `enroll` it afresh."""
    resp = cli.post(
        f"/api/runners/{runner_id}/reinstate",
        "POST /runners/{id}/reinstate",
        json_body={"by": by},
        on_status={404: f"unknown runner {runner_id}", 409: f"runner {runner_id} is not retired"},
    )
    body = resp.json()
    cli.show_lines(body, f"runner {runner_id} is reinstated — enroll it to mint a fresh token")


@runner_group.command("revoke-token", cls=FleetCommand)
@click.argument("runner_id")
@click.option("--by", "by", default="operator", help="Who is revoking (recorded on the revocation).")
def runner_revoke_token(cli: CliContext, runner_id: str, by: str) -> None:
    """Revoke a runner's token — it stays registered, and is refused until re-enrolled."""
    resp = cli.post(
        f"/api/runners/{runner_id}/token-revocations",
        "POST /runners/{id}/token-revocations",
        json_body={"by": by},
        on_status={404: f"unknown runner {runner_id}", 409: f"runner {runner_id} has no enrolled token"},
    )
    body = resp.json()
    cli.show_lines(body, f"revoked {runner_id}'s token — enroll it to mint a fresh one")
