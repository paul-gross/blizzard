"""``blizzard hub runner`` — operator verbs over one runner, each addressed by its hub-minted id."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import click
import httpx

from blizzard.foundation.roles import dto
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing, RunnerLine

#: How a hub from before ``hub runner add`` answers ``POST /api/runners`` — with no detail worth showing.
_PREDATES_ADD = frozenset({httpx.codes.NOT_FOUND, httpx.codes.METHOD_NOT_ALLOWED})


class RunnerListing(Listing):
    """Every runner's line, the name column as wide as the longest name shown so the state column lines up."""

    empty = "no runners — add one with `blizzard hub runner add <name>`"

    def line(self, row: Any) -> str:
        width = max([RunnerLine.NAME_WIDTH, *(len(RunnerLine(shown).name) for shown in self.rows)])
        return RunnerLine(row, name_width=width).line()


@dto
@dataclass(frozen=True)
class RunnerDetail:
    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        runner = RunnerLine(self.body)
        yield f"{self.body['runner_id']}  {runner.name}  {runner.connection}  ws={runner.workspace}"
        yield f"  added={self.body.get('added_at') or '-'} by {self.body.get('added_by') or '-'}"
        yield f"  hub_paused={self.body.get('hub_paused')}  locally_paused={self.body.get('locally_paused')}"
        if self.body.get("retired"):
            yield f"  retired={self.body.get('retired_at')} by {self.body.get('retired_by')}"


def _token_lines(token: str) -> tuple[str, str]:
    """A freshly minted bearer token, printed once as the line a runner directory's ``.env`` takes."""
    return "bearer token, shown only once — put this line in the runner directory's .env:", f"BZ_HUB_TOKEN={token}"


@click.group("runner")
def runner_group() -> None:
    """Operator verbs over one runner: adding it, its liveness, its pause brake, and retirement.

    Every verb but `add` and `list` takes a runner's hub-minted id, which `list` shows beside each
    name. A name is display-only and never resolves to a runner."""


@runner_group.command("add", cls=FleetCommand)
@click.argument("name")
def runner_add(cli: CliContext, name: str) -> None:
    """Add a runner under NAME; prints its hub-minted id and bearer token exactly once.

    The runner stays never connected until it first registers with that token; from then on its
    own configured name replaces NAME. Names need not be unique. `blizzard runner init` adds a
    runner and installs its token in one step."""
    resp = cli.send("post", "/api/runners", json_body={"name": name})
    if resp.status_code in _PREDATES_ADD:
        raise click.ClickException(f"this hub ({cli.hub_url}) predates `hub runner add`")
    cli.check(resp, "POST /runners", on_status={422: "a runner's name must not be blank"})
    body = resp.json()
    cli.show_lines(
        body,
        f"added runner {body['runner_id']} ({body['runner_name']}) — never connected",
        *_token_lines(body["token"]),
    )


@runner_group.command("list", cls=FleetCommand)
@click.option("--all", "include_retired", is_flag=True, default=False, help="Include retired runners, marked.")
def runner_list(cli: CliContext, include_retired: bool) -> None:
    """The fleet registry — every added runner, oldest first: its id, name, connection and paused state.

    A runner added but never registered shows as never-connected. Excludes a retired runner
    unless --all."""
    params = {"include_retired": "true"} if include_retired else None
    body = cli.get("/api/runners", "GET /runners", params=params).json()
    cli.show(body, RunnerListing(body.get("runners", [])))


@runner_group.command("show", cls=FleetCommand)
@click.argument("runner_id")
def runner_show(cli: CliContext, runner_id: str) -> None:
    """One runner's connection and paused state, by RUNNER_ID — symmetric with ``runner list``."""
    resp = cli.get(f"/api/runners/{runner_id}", "GET /runners/{id}", on_status={404: f"unknown runner {runner_id}"})
    body = resp.json()
    cli.show(body, RunnerDetail(body))


@runner_group.command("pause", cls=FleetCommand)
@click.argument("runner_id")
@click.option("--by", "by", default="operator", help="Who is pausing (recorded on the fact).")
def runner_pause(cli: CliContext, runner_id: str, by: str) -> None:
    """Pause runner RUNNER_ID — it stops claiming new work; in-flight chunks run on."""
    _set_runner_pause(cli, runner_id, verb="pause", by=by)


@runner_group.command("resume", cls=FleetCommand)
@click.argument("runner_id")
@click.option("--by", "by", default="operator", help="Who is resuming (recorded on the fact).")
def runner_resume(cli: CliContext, runner_id: str, by: str) -> None:
    """Resume paused runner RUNNER_ID — it claims work again on its next pull."""
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
    """Rotate RUNNER_ID's bearer token; prints the new plaintext exactly once.

    A thin client of ``POST /runners/{id}/enrollments``. The token it replaces stops resolving
    immediately, the one `add` minted included. RUNNER_ID must already be added at the hub
    (404 otherwise) and not retired (409 — `reinstate` it first)."""
    resp = cli.post(
        f"/api/runners/{runner_id}/enrollments",
        "POST /runners/{id}/enrollments",
        on_status={404: f"unknown runner {runner_id}", 409: f"runner {runner_id} is retired"},
    )
    body = resp.json()
    cli.show_lines(body, f"enrolled {runner_id} — its previous token no longer resolves", *_token_lines(body["token"]))


@runner_group.command("retire", cls=FleetCommand)
@click.argument("runner_id")
@click.option("--force", is_flag=True, default=False, help="Release every chunk the runner still holds.")
@click.option("--by", "by", default="operator", help="Who is retiring (recorded on the fact).")
def runner_retire(cli: CliContext, runner_id: str, force: bool, by: str) -> None:
    """Retire runner RUNNER_ID — revoke its token and refuse its claims and registrations.

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
    """Reinstate retired runner RUNNER_ID — it stays unenrolled; `enroll` it afresh."""
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
    """Revoke runner RUNNER_ID's token — it stays added, and is refused until re-enrolled."""
    resp = cli.post(
        f"/api/runners/{runner_id}/token-revocations",
        "POST /runners/{id}/token-revocations",
        json_body={"by": by},
        on_status={404: f"unknown runner {runner_id}", 409: f"runner {runner_id} has no enrolled token"},
    )
    body = resp.json()
    cli.show_lines(body, f"revoked {runner_id}'s token — enroll it to mint a fresh one")
