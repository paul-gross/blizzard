"""``blizzard hub routine`` — operator verbs over routines: create, list, inspect,
edit, run, and the ``trend``/``sweeps``/``proposal-counts`` gardening reports."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import click
import httpx

from blizzard.cli.window import since_option, until_option, utc_query_value
from blizzard.foundation.roles import dto
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing, ProposalOrigin

_ROUTINE_MODEL_HELP = (
    "The routine's default model preference. Repeatable and ORDERED — the first entry "
    "that resolves at session mint wins."
)
_ROUTINE_HARNESSES_HELP = (
    "The routine's default harness preference. Repeatable and ORDERED — the first entry "
    "that resolves at session mint wins."
)


class RoutineListing(Listing):
    empty = "no routines yet"

    def line(self, row: Any) -> str:
        marker = "  retired" if row.get("retired") else ""
        return (
            f"{row['routine_id']}  name={row['name']}  graph={row['graph_name']}  "
            f"scope={row['default_scope_slug']}{marker}"
        )


@dto
@dataclass(frozen=True)
class RoutineDetail:
    body: dict[str, Any]
    scopes: Sequence[str]

    def lines(self) -> Iterator[str]:
        body = self.body
        yield f"{body['routine_id']}  name={body['name']}  graph={body['graph_name']}"
        yield f"  default scope: {body['default_scope_slug']}"
        models = ", ".join(body.get("default_model") or []) or "-"
        yield f"  default model: {models}   default effort: {body.get('default_effort') or '-'}"
        harnesses = ", ".join(body.get("default_harnesses") or []) or "-"
        yield f"  default harnesses: {harnesses}"
        yield f"  scopes: {', '.join(self.scopes)}"


@click.group("routine")
def routine_group() -> None:
    """Operator verbs over routines: create, list, inspect, edit."""


@routine_group.command("create", cls=FleetCommand)
@click.argument("name")
@click.argument("graph_name")
@click.argument("default_scope_slug")
@click.option("--model", "default_model", multiple=True, help=_ROUTINE_MODEL_HELP)
@click.option("--effort", "default_effort", default=None, help="The routine's default effort.")
@click.option("--harnesses", "default_harnesses", multiple=True, help=_ROUTINE_HARNESSES_HELP)
def routine_create(
    cli: CliContext,
    name: str,
    graph_name: str,
    default_scope_slug: str,
    default_model: tuple[str, ...],
    default_effort: str | None,
    default_harnesses: tuple[str, ...],
) -> None:
    """Mint a routine named NAME, running GRAPH_NAME with DEFAULT_SCOPE_SLUG's scope.

    DEFAULT_SCOPE_SLUG mints a fresh scope if unseen. GRAPH_NAME must resolve to
    an enabled graph."""
    resp = cli.send(
        "post",
        "/api/routines",
        json_body={
            "name": name,
            "graph_name": graph_name,
            "default_scope_slug": default_scope_slug,
            "default_model": list(default_model),
            "default_effort": default_effort,
            "default_harnesses": list(default_harnesses),
        },
        door=True,
    )
    if resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
        raise click.ClickException(f"routine rejected: {cli.detail(resp, 'validation failed')}")
    cli.check(resp, "POST /routines")
    body = resp.json()
    cli.show_lines(body, f"minted routine {body['routine_id']}")


@routine_group.command("list", cls=FleetCommand)
@click.option("--include-retired", is_flag=True, default=False, help="Include retired routines.")
def routine_list(cli: CliContext, include_retired: bool) -> None:
    """List every routine, newest first — routine_id, name, graph, default scope.

    Excludes a retired routine unless --include-retired."""
    params = {"include_retired": "true"} if include_retired else None
    rows = cli.get("/api/routines", "GET /routines", params=params).json()
    cli.show(rows, RoutineListing(rows))


@routine_group.command("show", cls=FleetCommand)
@click.argument("routine_id")
def routine_show(cli: CliContext, routine_id: str) -> None:
    """One routine's whole record — name, graph, default scope, model/effort defaults,
    and its linked scopes."""
    resp = cli.get(
        f"/api/routines/{routine_id}", "GET /routines/{id}", on_status={404: f"unknown routine {routine_id}"}
    )
    body = resp.json()
    scopes = cli.get(
        f"/api/routines/{routine_id}/scopes",
        "GET /routines/{id}/scopes",
        on_status={404: f"unknown routine {routine_id}"},
    ).json()
    cli.show({**body, "scopes": scopes}, RoutineDetail(body, scopes))


@routine_group.command("edit", cls=FleetCommand)
@click.argument("routine_id")
@click.option("--graph", "graph_name", default=None, help="The routine's graph name.")
@click.option("--scope", "default_scope_slug", default=None, help="The routine's default scope slug.")
@click.option("--model", "default_model", multiple=True, help=_ROUTINE_MODEL_HELP)
@click.option("--effort", "default_effort", default=None, help="The routine's default effort.")
@click.option("--harnesses", "default_harnesses", multiple=True, help=_ROUTINE_HARNESSES_HELP)
def routine_edit(
    cli: CliContext,
    routine_id: str,
    graph_name: str | None,
    default_scope_slug: str | None,
    default_model: tuple[str, ...],
    default_effort: str | None,
    default_harnesses: tuple[str, ...],
) -> None:
    """Change ROUTINE_ID's graph, default scope, or model/effort/harnesses defaults; only the
    options given change, and its name never changes here."""
    resp = cli.get(
        f"/api/routines/{routine_id}", "GET /routines/{id}", on_status={404: f"unknown routine {routine_id}"}
    )
    given: dict[str, Any] = {
        "graph_name": graph_name,
        "default_scope_slug": default_scope_slug,
        "default_model": list(default_model) or None,
        "default_effort": default_effort,
        "default_harnesses": list(default_harnesses) or None,
    }
    body = {"name": resp.json()["name"], **{k: v for k, v in given.items() if v is not None}}
    resp = cli.send("patch", f"/api/routines/{routine_id}", json_body=body, door=True)
    if resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
        raise click.ClickException(f"routine edit rejected: {cli.detail(resp, 'validation failed')}")
    cli.check(resp, "PATCH /routines/{id}", on_status={404: f"unknown routine {routine_id}"})
    body = resp.json()
    cli.show_lines(body, f"routine {routine_id} updated")


@routine_group.group("scope")
def routine_scope_group() -> None:
    """Manage a routine's scope membership: add, remove."""


@routine_scope_group.command("add", cls=FleetCommand)
@click.argument("routine_id")
@click.argument("scope_slug")
def routine_scope_add(cli: CliContext, routine_id: str, scope_slug: str) -> None:
    """Link SCOPE_SLUG into ROUTINE_ID's own scope set; idempotent.

    404 on an unknown ROUTINE_ID or a well-formed but unknown SCOPE_SLUG; 422 on a
    malformed SCOPE_SLUG."""
    resp = cli.send("put", f"/api/routines/{routine_id}/scopes/{scope_slug}", door=True)
    if resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
        raise click.ClickException(f"scope add rejected: {cli.detail(resp, 'validation failed')}")
    cli.check(
        resp,
        "PUT /routines/{id}/scopes/{slug}",
        on_status={404: f"unknown routine {routine_id} or scope {scope_slug}"},
    )
    cli.show_lines(
        {"routine_id": routine_id, "scope_slug": scope_slug}, f"linked scope {scope_slug!r} to routine {routine_id}"
    )


@routine_scope_group.command("remove", cls=FleetCommand)
@click.argument("routine_id")
@click.argument("scope_slug")
def routine_scope_remove(cli: CliContext, routine_id: str, scope_slug: str) -> None:
    """Unlink SCOPE_SLUG from ROUTINE_ID's own scope set; idempotent.

    404 on an unknown ROUTINE_ID or a well-formed but unknown SCOPE_SLUG; 422 on a
    malformed SCOPE_SLUG, or on naming ROUTINE_ID's own default scope — always a member
    of its own set."""
    resp = cli.send("delete", f"/api/routines/{routine_id}/scopes/{scope_slug}", door=True)
    if resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
        raise click.ClickException(f"scope remove rejected: {cli.detail(resp, 'validation failed')}")
    cli.check(
        resp,
        "DELETE /routines/{id}/scopes/{slug}",
        on_status={404: f"unknown routine {routine_id} or scope {scope_slug}"},
    )
    cli.show_lines(
        {"routine_id": routine_id, "scope_slug": scope_slug},
        f"unlinked scope {scope_slug!r} from routine {routine_id}",
    )


def _resolve_routine_id(cli: CliContext, name: str) -> str:
    """Resolve NAME to its routine_id, including a retired routine — so a
    retired routine resolves and reaches the domain's own retired refusal, not an
    ``unknown routine`` one."""
    rows = cli.get("/api/routines", "GET /routines", params={"include_retired": "true"}).json()
    matched = next((r for r in rows if r["name"] == name), None)
    if matched is None:
        raise click.ClickException(f"unknown routine {name!r}")
    return str(matched["routine_id"])


@routine_group.command("retire", cls=FleetCommand)
@click.argument("name")
@click.option("--by", "by", default="operator", help="Who is retiring (recorded on the fact).")
def routine_retire(cli: CliContext, name: str, by: str) -> None:
    """Retire routine NAME — a reversible brake; in-flight runs are untouched."""
    _set_routine_lifecycle(cli, name, verb="retire", by=by)


@routine_group.command("enable", cls=FleetCommand)
@click.argument("name")
@click.option("--by", "by", default="operator", help="Who is re-enabling (recorded on the fact).")
def routine_enable(cli: CliContext, name: str, by: str) -> None:
    """Re-enable a retired routine NAME."""
    _set_routine_lifecycle(cli, name, verb="enable", by=by)


def _set_routine_lifecycle(cli: CliContext, name: str, *, verb: str, by: str) -> None:
    routine_id = _resolve_routine_id(cli, name)
    resp = cli.post(
        f"/api/routines/{routine_id}/{verb}",
        f"POST /routines/{{id}}/{verb}",
        json_body={"by": by},
        on_status={404: f"unknown routine {name!r}"},
        door=True,
    )
    body = resp.json()
    state = "retired" if body.get("retired") else "enabled"
    cli.show_lines(body, f"routine {name!r} is now {state}")


@routine_group.command("run", cls=FleetCommand)
@click.argument("name")
@click.option(
    "--scope",
    "scope_slug",
    default=None,
    help="Override the routine's default scope slug — must already be linked into the routine's own set.",
)
@click.option(
    "--mode",
    type=click.Choice(["full", "delta"]),
    default="full",
    help="delta downgrades to full when the routine/scope pair has recorded no baseline.",
)
@click.option("--note", default=None, help='A note appended to the run\'s charge as a "This run" section.')
def routine_run(cli: CliContext, name: str, scope_slug: str | None, mode: str, note: str | None) -> None:
    """Mint and ingest a hub work item from routine NAME, in one act.

    The chunk rests not_ready until `blizzard hub chunk promote`. NAME resolves through
    the routine list, including a retired routine — so it reaches the domain's own
    retired refusal."""
    routine_id = _resolve_routine_id(cli, name)
    resp = cli.send(
        "post",
        f"/api/routines/{routine_id}/run",
        json_body={"scope_slug": scope_slug, "mode": mode, "note": note},
    )
    if resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
        raise click.ClickException(f"run rejected: {cli.detail(resp, 'validation failed')}")
    if resp.status_code == httpx.codes.CONFLICT:
        raise click.ClickException(f"run refused: {cli.detail(resp, 'conflict')}")
    cli.check(
        resp,
        "POST /routines/{id}/run",
        on_status={404: f"unknown routine {name!r}", 503: f"routine {name!r} refused to run"},
    )
    body = resp.json()
    lines = [f"minted {body['chunk_id']} from routine {name!r} — mode={body['effective_mode']}"]
    if body["downgraded"]:
        lines.append("note: requested delta downgraded to full — the routine/scope pair has recorded no baseline yet")
    lines.append(f"not yet claimable — promote it with: blizzard hub chunk promote {body['chunk_id']}")
    cli.show_lines(body, *lines)


@dto
@dataclass(frozen=True)
class TrendDetail:
    """`routine trend`'s own render — per-period counts, then the age cut."""

    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        body = self.body
        yield (f"{body['routine_name']}  {body['since']} .. {body['until']}  period_days={body['period_days']}")
        for period in body["periods"]:
            exits = ", ".join(f"{kind}={count}" for kind, count in period["exits"].items())
            yield (
                f"  {period['period_start']} .. {period['period_end']}  created={period['created']}  "
                f"outflow={period['outflow']}  withdrawn={period['withdrawn']}  reopened={period['reopened']}  "
                f"({exits})"
            )
        age = body["age"]
        yield (
            f"  age boundary={age['boundary']}  recent={age['recent']}  older={age['older']}  "
            f"unattributed={age['unattributed']}"
        )


@routine_group.command("trend", cls=FleetCommand)
@click.argument("name")
@since_option(required=True)
@until_option(required=True)
@click.option(
    "--introduced-boundary",
    "introduced_boundary",
    required=True,
    type=click.DateTime(),
    help="The recent/older cut, in local time — a created finding's own introduced instant, not this window's.",
)
@click.option("--period-days", "period_days", default=7, type=int, help="Each period's width, in days (default 7).")
def routine_trend(
    cli: CliContext, name: str, since: datetime, until: datetime, introduced_boundary: datetime, period_days: int
) -> None:
    """NAME's finding inflow-against-outflow over --since/--until: per-period created and
    per-kind exit counts, the outflow/withdrawn roll-ups, and the introduced-age cut
    against --introduced-boundary."""
    resp = cli.send(
        "get",
        "/api/routines/trend",
        params={
            "routine": name,
            "since": utc_query_value(since),
            "until": utc_query_value(until),
            "introduced_boundary": utc_query_value(introduced_boundary),
            "period_days": str(period_days),
        },
    )
    if resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
        raise click.ClickException(f"trend rejected: {cli.detail(resp, 'validation failed')}")
    cli.check(resp, "GET /routines/trend")
    body = resp.json()
    cli.show(body, TrendDetail(body))


@dto
@dataclass(frozen=True)
class SweepsDetail:
    """`routine sweeps`'s own render — the last-swept table (unwindowed), then the
    measurement series, over the same window `trend`'s own ``--since``/``--until``
    name."""

    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        body = self.body
        yield f"{body['routine_name']}  measurement window {body['since']} .. {body['until']}"
        yield "  last swept:"
        for row in body["last_swept"]:
            if row["finding_set_id"] is None:
                yield f"    {row['scope_slug']}: never"
                continue
            revisions = ", ".join(f"{repo}@{rev}" for repo, rev in sorted(row["revisions"].items()))
            yield (
                f"    {row['scope_slug']}: {row['produced_at']}  {row['finding_set_id']}  "
                f"({revisions or 'no repositories recorded'})"
            )
        yield "  measurements:"
        for reading in body["measurements"]:
            yield f"    {reading['produced_at']}  {reading['scope_slug']}: {reading['measurement']}"


@routine_group.command("sweeps", cls=FleetCommand)
@click.argument("name")
@since_option(required=True)
@until_option(required=True)
def routine_sweeps(cli: CliContext, name: str, since: datetime, until: datetime) -> None:
    """NAME's per-scope last-swept table — its declared scope set, retired scopes
    filtered out unless already swept while linked — and its measurement series over
    --since/--until."""
    routine_id = _resolve_routine_id(cli, name)
    resp = cli.send(
        "get",
        f"/api/routines/{routine_id}/sweeps",
        params={"since": utc_query_value(since), "until": utc_query_value(until)},
    )
    if resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
        raise click.ClickException(f"sweeps rejected: {cli.detail(resp, 'validation failed')}")
    cli.check(resp, "GET /routines/{id}/sweeps", on_status={404: f"unknown routine {name!r}"})
    body = resp.json()
    cli.show(body, SweepsDetail(body))


@dto
@dataclass(frozen=True)
class ProposalCountsDetail:
    """`routine proposal-counts`'s own render — one line per class/origin
    pair, `created` echoed as the open/passed/accepted-with-item/accepted-without-item sum."""

    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        body = self.body
        routine = body.get("routine")
        scope = f"  routine={routine}" if routine else ""
        yield f"{body['since']} .. {body['until']}{scope}"
        rows = body["rows"]
        if not rows:
            yield "  no proposals in this window"
            return
        for row in rows:
            yield (
                f"  {row['class']}  {ProposalOrigin(row).rendered}  created={row['created']}  "
                f"open={row['open']}  passed={row['passed']}  accepted(item)={row['accepted_with_item']}  "
                f"accepted(no item)={row['accepted_without_item']}"
            )


@routine_group.command("proposal-counts", cls=FleetCommand)
@click.argument("name", required=False, default=None)
@since_option(required=True)
@until_option(required=True)
@click.option("--origin", type=click.Choice(["routine-run", "operator"]), default=None, help="Narrow to one origin.")
def routine_proposal_counts(
    cli: CliContext, name: str | None, since: datetime, until: datetime, origin: str | None
) -> None:
    """NAME's garden-proposal counts over --since/--until, split into open/passed/
    accepted-with-item/accepted-without-item per class, with created as their sum; omit
    NAME to see every routine's rows at once."""
    params = {"since": utc_query_value(since), "until": utc_query_value(until)}
    if name is not None:
        params["routine"] = name
    if origin is not None:
        params["origin"] = origin
    resp = cli.send("get", "/api/routines/proposal-counts", params=params)
    if resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
        raise click.ClickException(f"proposal counts rejected: {cli.detail(resp, 'validation failed')}")
    on_status = {404: f"unknown routine {name!r}"} if name is not None else None
    cli.check(resp, "GET /routines/proposal-counts", on_status=on_status)
    body = resp.json()
    cli.show(body, ProposalCountsDetail(body))
