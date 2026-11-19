"""``blizzard hub runner`` — operator verbs over one runner, each addressed by its hub-minted id."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, cast

import click
from pydantic import TypeAdapter

from blizzard.cli.get_group import GetGroup
from blizzard.cli.refusal import refusal_of
from blizzard.foundation.roles import dto
from blizzard.foundation.runner_token_names import hub_token_variable
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing, RunnerLine, RunnerRow
from blizzard.wire.queue import NextClaimEntry, NextClaimPassedEntry, NextClaimResponse, NextClaimScenario
from blizzard.wire.runner import (
    RunnerAddResponse,
    RunnerEnrollmentResponse,
    RunnerRegistryListResponse,
    RunnerRegistryView,
    RunnerRetireResponse,
    RunnerTokenRevocationResponse,
)

#: How a hub from before ``hub runner add`` answers ``POST /api/runners`` — with no detail worth showing.
_PREDATES_ADD = frozenset({HTTPStatus.NOT_FOUND, HTTPStatus.METHOD_NOT_ALLOWED})

_ADDED = TypeAdapter(RunnerAddResponse)
_ENROLLED = TypeAdapter(RunnerEnrollmentResponse)
_REGISTRY = TypeAdapter(RunnerRegistryListResponse)
_RUNNER = TypeAdapter(RunnerRegistryView)
_NEXT_CLAIM = TypeAdapter(NextClaimResponse)
_RETIRED = TypeAdapter(RunnerRetireResponse)
_REVOKED = TypeAdapter(RunnerTokenRevocationResponse)


class RunnerListing(Listing):
    """Every runner's line, the name column as wide as the longest name shown so the state column lines up."""

    empty = "no runners — add one with `blizzard hub runner add <name>`"

    def line(self, row: Any) -> str:
        width = max([RunnerLine.NAME_WIDTH, *(len(RunnerLine(shown).name) for shown in self.rows)])
        return RunnerLine(row, name_width=width).line()


@dto
@dataclass(frozen=True)
class RunnerDetail:
    row: RunnerRow
    view: RunnerRegistryView

    def lines(self) -> Iterator[str]:
        runner = RunnerLine(self.row)
        view = self.view
        yield f"{view.runner_id}  {runner.name}  {runner.connection}  ws={runner.workspace}"
        yield f"  added={view.added_at or '-'} by {view.added_by or '-'}"
        yield f"  hub_paused={view.hub_paused}  locally_paused={view.locally_paused}"
        if view.retired:
            yield f"  retired={view.retired_at} by {view.retired_by}"


def _claim_entry(entry: NextClaimEntry) -> str:
    urgency = entry.urgency.slug if entry.urgency else "-"
    return f"{entry.position}  {entry.chunk_id}  urgency={urgency}"


def _pass_reason(entry: NextClaimPassedEntry) -> str:
    """Why the walk passed a chunk; a blocked one names its first prerequisite and how many more it has."""
    reason = str(entry.reason)
    if reason != "blocked" or not entry.prerequisite:
        return reason
    others = entry.prerequisite_count - 1
    more = f" (+{others} more)" if others > 0 else ""
    return f"blocked by {entry.prerequisite}{more}"


@dto
@dataclass(frozen=True)
class NextClaimDetail:
    """A runner's next claim as the hub accounts for it: the chunk it reaches for, then each chunk passed on the way,
    and how many more were passed than are listed."""

    body: NextClaimResponse

    def lines(self) -> Iterator[str]:
        yield "preparing at repository targets:"
        yield from _scenario_lines(self.body)
        yield "not preparing at repository targets:"
        alternative = self.body.without_repository_targets
        if alternative is None:
            yield "  not reported by this hub"
            return
        excluded = alternative.excluded
        if excluded > 0:
            yield f"  {excluded} ahead never offered at repository targets"
        for line in _scenario_lines(alternative):
            yield f"  {line}"


def _scenario_lines(scenario: NextClaimResponse | NextClaimScenario) -> Iterator[str]:
    """One scenario's account: the chunk it reaches for, each chunk passed on the way, and how many more were passed
    than are listed."""
    upcoming = scenario.next
    if upcoming:
        yield f"next  {_claim_entry(upcoming)}"
    else:
        yield "next  - (nothing in claim order this runner can use)"
    passed = scenario.passed
    for entry in passed:
        yield f"  passed  {_claim_entry(entry)}  {_pass_reason(entry)}"
    remainder = scenario.passed_total - len(passed)
    if remainder > 0:
        yield f"  ... and {remainder} more passed"


def _token_lines(runner_name: str, token: str, *, selector: str) -> tuple[str, str, str]:
    """A freshly minted bearer token on a line of its own, framed by how a host takes it up."""
    return (
        "bearer token, shown only once — on the runner's host, pipe it on stdin to "
        f"`blizzard runner token set --runner {selector}`:",
        token,
        "(a host from before the sealed token store reads it from its directory's .env instead, as "
        f"{hub_token_variable(runner_name)})",
    )


@click.group("runner", cls=GetGroup)
def runner_group() -> None:
    """Register, pause, credential, and retire runners.

    Every verb but `add` and `list` takes a runner's hub-minted id, which `list` shows beside each
    name. A name is display-only and never resolves to a runner."""


@runner_group.command("add", cls=FleetCommand)
@click.argument("name")
@click.option(
    "--project",
    "projects",
    multiple=True,
    metavar="PROJECT",
    help="A project the runner serves, by id or slug (repeatable). Without it, the runner serves the tenant's "
    "project when the tenant holds one, and none when it holds several.",
)
@click.option(
    "--host",
    "host_id",
    default=None,
    metavar="HOST_ID",
    help="Add the runner under this host (an `hs_` id from `blizzard hub runner-host list`); refused when it is "
    "unknown or retired.",
)
def runner_add(cli: CliContext, name: str, projects: tuple[str, ...], host_id: str | None) -> None:
    """Add a runner under NAME; prints its hub-minted id and bearer token exactly once.

    The runner stays never connected until it first registers with that token; from then on its
    own configured name replaces NAME. Names need not be unique. `blizzard runner init` adds a
    runner and installs its token in one step."""
    request: dict[str, object] = {"name": name}
    if projects:
        request["projects"] = list(projects)
    if host_id is not None:
        request["host_id"] = host_id
    resp = cli.send("post", "/api/runners", json_body=request)
    # Old-hub fallback for a body with no problem type; removed with the other old-hub fallbacks at 0.6.
    # ast-grep-ignore: bzh:cli-refusal-body-confined
    if resp.status_code in _PREDATES_ADD and refusal_of(resp).type is None and host_id is None:
        raise click.ClickException(f"this hub ({cli.hub_url}) predates `hub runner add`")
    cli.check(
        resp,
        "POST /runners",
    )
    raw, added = cli.decode(resp, _ADDED, "POST /runners")
    cli.show_lines(
        raw,
        f"added runner {added.runner_id} ({added.runner_name}) — never connected",
        # A just-added runner's host declares it by name; its id lands in the host file only once it joins.
        *_token_lines(added.runner_name, added.token, selector=added.runner_name),
    )


@runner_group.command("list", cls=FleetCommand)
@click.option("--all", "include_retired", is_flag=True, default=False, help="Include retired runners, marked.")
def runner_list(cli: CliContext, include_retired: bool) -> None:
    """The fleet registry — every added runner, oldest first: its id, name, connection and paused state.

    A runner added but never registered shows as never-connected. Excludes a retired runner
    unless --all."""
    params = {"include_retired": "true"} if include_retired else None
    raw, registry = cli.decode(cli.get("/api/runners", "GET /runners", params=params), _REGISTRY, "GET /runners")
    rows = cast(list[RunnerRow], raw["runners"]) if registry.runners else []
    cli.show(raw, RunnerListing(rows))


@runner_group.command("get", cls=FleetCommand)
@click.argument("runner_id")
def runner_show(cli: CliContext, runner_id: str) -> None:
    """One runner's connection and paused state, by RUNNER_ID — symmetric with ``runner list``."""
    resp = cli.get(f"/api/runners/{runner_id}", "GET /runners/{id}")
    raw, view = cli.decode(resp, _RUNNER, "GET /runners/{id}")
    cli.show(raw, RunnerDetail(cast(RunnerRow, raw), view))


@runner_group.command("next", cls=FleetCommand)
@click.argument("runner_id")
def runner_next(cli: CliContext, runner_id: str) -> None:
    """The chunk RUNNER_ID reaches for next, and each chunk it passes on the way with its urgency and why — the hub's
    best account from what it holds of the runner, not a promise of what the runner claims."""
    resp = cli.get(f"/api/runners/{runner_id}/next-claim", "GET /runners/{id}/next-claim")
    raw, claim = cli.decode(resp, _NEXT_CLAIM, "GET /runners/{id}/next-claim")
    cli.show(raw, NextClaimDetail(claim))


@runner_group.command("pause", cls=FleetCommand)
@click.argument("runner_id")
@click.option("--by", "by", default="operator", help="Who is pausing, recorded as the actor.")
def runner_pause(cli: CliContext, runner_id: str, by: str) -> None:
    """Pause runner RUNNER_ID — it stops claiming new work; in-flight chunks run on."""
    _set_runner_pause(cli, runner_id, verb="pause", by=by)


@runner_group.command("resume", cls=FleetCommand)
@click.argument("runner_id")
@click.option("--by", "by", default="operator", help="Who is resuming, recorded as the actor.")
def runner_resume(cli: CliContext, runner_id: str, by: str) -> None:
    """Resume paused runner RUNNER_ID — it claims work again on its next pull."""
    _set_runner_pause(cli, runner_id, verb="resume", by=by)


def _set_runner_pause(cli: CliContext, runner_id: str, *, verb: str, by: str) -> None:
    resp = cli.post(
        f"/api/runners/{runner_id}/{verb}",
        f"POST /runners/{{id}}/{verb}",
        json_body={"by": by},
    )
    raw, runner = cli.decode(resp, _RUNNER, f"POST /runners/{{id}}/{verb}")
    state = "paused" if runner.hub_paused else "running"
    lines = [f"runner {runner_id} is now {state} (at the hub)"]
    if runner.locally_paused:
        # Resuming here cannot clear the runner's own brake, so don't imply it did.
        lines.append(f"note: runner {runner_id} also paused itself — clear that with `blizzard runner start`")
    cli.show_lines(raw, *lines)


@runner_group.command("enroll", cls=FleetCommand)
@click.argument("runner_id")
def runner_enroll(cli: CliContext, runner_id: str) -> None:
    """Rotate RUNNER_ID's bearer token; prints the new plaintext exactly once.

    The previous token, the one `add` issued included, stops working immediately. Refused when
    RUNNER_ID is unknown or retired — `reinstate` it first."""
    # Read first, so a failed read leaves the token unrotated:
    # test_enroll_rotates_nothing_when_the_registry_entry_cannot_be_read.
    _, entry = cli.decode(cli.get(f"/api/runners/{runner_id}", "GET /runners/{id}"), _RUNNER, "GET /runners/{id}")
    resp = cli.post(
        f"/api/runners/{runner_id}/enrollments",
        "POST /runners/{id}/enrollments",
    )
    raw, enrolled = cli.decode(resp, _ENROLLED, "POST /runners/{id}/enrollments")
    cli.show_lines(
        raw,
        f"enrolled {runner_id} — its previous token no longer resolves",
        *_token_lines(entry.runner_name, enrolled.token, selector=runner_id),
    )


@runner_group.command("retire", cls=FleetCommand)
@click.argument("runner_id")
@click.option("--force", is_flag=True, default=False, help="Release every chunk the runner still holds.")
@click.option("--by", "by", default="operator", help="Who is retiring, recorded as the actor.")
def runner_retire(cli: CliContext, runner_id: str, force: bool, by: str) -> None:
    """Retire runner RUNNER_ID — revoke its token and refuse its claims and registrations.

    Refused while it holds chunks unless --force, which releases them. Re-running
    finishes a partial release. Stop the runner process too: the hub refuses it from here on."""
    resp = cli.post(
        f"/api/runners/{runner_id}/retire",
        "POST /runners/{id}/retire",
        json_body={"by": by, "force": force},
    )
    raw, retired = cli.decode(resp, _RETIRED, "POST /runners/{id}/retire")
    lines = [f"runner {runner_id} is retired"]
    if retired.released_chunk_ids:
        lines.append(f"released: {', '.join(retired.released_chunk_ids)}")
    cli.show_lines(raw, *lines)


@runner_group.command("reinstate", cls=FleetCommand)
@click.argument("runner_id")
@click.option("--by", "by", default="operator", help="Who is reinstating, recorded as the actor.")
def runner_reinstate(cli: CliContext, runner_id: str, by: str) -> None:
    """Reinstate retired runner RUNNER_ID — it stays unenrolled; `enroll` it afresh."""
    resp = cli.post(
        f"/api/runners/{runner_id}/reinstate",
        "POST /runners/{id}/reinstate",
        json_body={"by": by},
    )
    raw, _ = cli.decode(resp, _RUNNER, "POST /runners/{id}/reinstate")
    cli.show_lines(raw, f"runner {runner_id} is reinstated — enroll it to mint a fresh token")


@runner_group.command("revoke-token", cls=FleetCommand)
@click.argument("runner_id")
@click.option("--by", "by", default="operator", help="Who is revoking, recorded as the actor.")
def runner_revoke_token(cli: CliContext, runner_id: str, by: str) -> None:
    """Revoke runner RUNNER_ID's token — it stays added, and is refused until re-enrolled."""
    resp = cli.post(
        f"/api/runners/{runner_id}/token-revocations",
        "POST /runners/{id}/token-revocations",
        json_body={"by": by},
    )
    raw, _ = cli.decode(resp, _REVOKED, "POST /runners/{id}/token-revocations")
    cli.show_lines(raw, f"revoked {runner_id}'s token — enroll it to mint a fresh one")
