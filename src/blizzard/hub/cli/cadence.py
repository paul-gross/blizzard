"""``blizzard hub cadence`` — operator commands over cadences and their chores.

A cadence is addressed by its slug within its project. A ``chore`` verb is an edit of its cadence: it reads the
cadence, rewrites its whole chore list, and writes it back on the revision it read."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import click

from blizzard.cli.get_group import GetGroup
from blizzard.cli.refusal import refusal_of
from blizzard.foundation.roles import dto
from blizzard.foundation.run_mode import RunMode
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext, project_required
from blizzard.hub.cli.flag_gate import hub_flag_on
from blizzard.hub.cli.project_option import project_option, project_path, project_query
from blizzard.hub.cli.views import Listing
from blizzard.wire.problem import REVISION_CONFLICT

_FLAG_OFF_NOTICE = "note: cadences mint nothing until the flag cadences is on"
_ANCHORS = click.Choice(["scheduled", "started", "finished"])
_STATES = ("due", "running", "waiting", "stopped")
_MODES = click.Choice([mode.value for mode in RunMode])


def _rhythm(row: dict[str, Any]) -> str:
    parts: list[str] = []
    if row.get("schedule"):
        parts.append(f"cron={row['schedule']['cron']!r} zone={row['schedule']['zone']}")
    if row.get("spacing_minutes") is not None:
        parts.append(f"spacing={row['spacing_minutes']}m anchor={row['anchor']}")
    return "  ".join(parts) or "no rhythm"


class CadenceListing(Listing):
    empty = "no cadences yet"

    def line(self, row: Any) -> str:
        marker = "  retired" if row.get("retired") else ""
        attention = f"  attention={len(row['attention'])}" if row.get("attention") else ""
        return (
            f"{row['cadence_id']}  slug={row['slug']}  name={row['name']}  {_rhythm(row)}  "
            f"chores={len(row.get('chores') or [])}{attention}{marker}"
        )


def _scopes_text(scopes: Any) -> str:
    if scopes is None:
        return "default scope"
    if scopes == "all":
        return "all scopes"
    return f"scopes={','.join(scopes)}"


@dto
@dataclass(frozen=True)
class CadenceDetail:
    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        body = self.body
        marker = "  retired" if body.get("retired") else ""
        yield f"{body['cadence_id']}  slug={body['slug']}  name={body['name']}  revision={body['revision']}{marker}"
        yield f"  rhythm: {_rhythm(body)}"
        chores: Sequence[dict[str, Any]] = body.get("chores") or []
        if not chores:
            yield "  chores: none"
        for chore in chores:
            held = "" if chore["promote"] else "  held"
            note = f"  note={chore['note']!r}" if chore.get("note") else ""
            yield (
                f"  chore {chore['key']}  {chore['chore_id']}  routine={chore['routine']}  "
                f"{_scopes_text(chore.get('scopes'))}  mode={chore['mode']}{held}{note}"
            )
        counts: dict[str, int] = body.get("errand_counts") or {}
        yield "  errands: " + "  ".join(f"{state}={counts.get(state, 0)}" for state in _STATES)
        attention: Sequence[dict[str, Any]] = body.get("attention") or []
        if not attention:
            yield "  attention: none"
        for entry in attention:
            where = "".join(f"  {label}={entry[label]}" for label in ("chore", "scope") if entry.get(label) is not None)
            yield f"  attention: {entry['reason']}{where}"


class ErrandListing(Listing):
    empty = "no errands"

    def line(self, row: Any) -> str:
        run = row.get("last_run")
        last = "last run: none"
        if run is not None:
            last = (
                f"last run: {run.get('chunk_id') or 'unresolved'} {run.get('status') or 'unknown'} "
                f"scheduled={run['scheduled_at']}"
            )
            if run.get("finished_at"):
                last += f" finished={run['finished_at']}"
        due = row.get("due_at") or "-"
        attention: list[dict[str, Any]] = row.get("attention") or []
        stopped = "".join(f"  {a['reason']}" for a in attention)
        return f"{row['chore']}  scope={row['scope']}  {row['state']}  due_at={due}  {last}{stopped}"


def _flag_notice() -> None:
    """Say on stderr, so ``--json`` output is unchanged, that cadences mint nothing while their flag is off."""
    if not hub_flag_on("cadences", click.get_current_context()):
        click.echo(_FLAG_OFF_NOTICE, err=True)


@click.group("cadence", cls=GetGroup)
def cadence_group() -> None:
    """Manage cadences — a project's rhythms — and the chores each keeps."""


def _schedule(cron: str | None, zone: str | None) -> dict[str, str] | None:
    if cron is None and zone is None:
        return None
    if cron is None or zone is None:
        raise click.UsageError("--cron and --zone are given together")
    return {"cron": cron, "zone": zone}


def _project(cli: CliContext, project: str | None) -> str:
    """``project`` as named, else the tenant's only project; refused naming the projects when it holds several."""
    if project is not None:
        return project
    projects = cli.get("/api/projects", "GET /projects").json()["projects"]
    if len(projects) != 1:
        raise project_required([{k: str(p[k]) for k in ("project_id", "slug", "name")} for p in projects])
    return str(projects[0]["project_id"])


@cadence_group.command("create", cls=FleetCommand)
@click.argument("slug")
@click.option("--name", "display_name", default=None, help="The cadence's display name; defaults to the slug.")
@click.option("--cron", default=None, help="A five-field cron expression, read as wall-clock time in --zone.")
@click.option("--zone", default=None, help="The IANA time zone --cron is read in.")
@click.option("--spacing", default=None, help="The least time between an errand's runs: a whole number and m, h, or d.")
@click.option("--anchor", type=_ANCHORS, default=None, help="What --spacing counts from; started when unstated.")
@project_option(required_by_hub=True)
def cadence_create(
    cli: CliContext,
    slug: str,
    display_name: str | None,
    cron: str | None,
    zone: str | None,
    spacing: str | None,
    anchor: str | None,
    project: str | None,
) -> None:
    """Create a cadence whose slug is SLUG, with no chores yet; it states a schedule, a spacing, or both.

    SLUG is the cadence's immutable handle within its project."""
    body: dict[str, Any] = {
        "slug": slug,
        "name": display_name,
        "schedule": _schedule(cron, zone),
        "spacing": spacing,
        "anchor": anchor,
    }
    resp = cli.send(
        "post",
        project_path(_project(cli, project), "/cadences"),
        json_body={k: v for k, v in body.items() if v is not None},
        door=True,
    )
    cli.check(resp, "POST /cadences")
    created = resp.json()
    cli.show_lines(created, f"created cadence {created['slug']!r} {created['cadence_id']}")


@cadence_group.command("list", cls=FleetCommand)
@click.option("--include-retired", is_flag=True, default=False, help="Include retired cadences.")
@project_option(required_by_hub=False)
def cadence_list(cli: CliContext, include_retired: bool, project: str | None) -> None:
    """List every cadence — its id, slug, name, rhythm, chore count, and attention.

    Excludes a retired cadence unless --include-retired."""
    params = {"include_retired": "true"} if include_retired else {}
    rows = cli.get("/api/cadences", "GET /cadences", params=(params | project_query(project)) or None).json()
    _flag_notice()
    cli.show(rows, CadenceListing(rows))


def _resolve_cadence(cli: CliContext, slug: str, project: str | None) -> dict[str, Any]:
    """The cadence SLUG within ``project``, a retired one included. With no project named, SLUG resolves only in a
    tenant holding one project: a slug is unique within its project alone."""
    params = {"include_retired": "true"} | project_query(_project(cli, project))
    rows = cli.get("/api/cadences", "GET /cadences", params=params).json()
    matched = next((r for r in rows if r["slug"] == slug), None)
    if matched is None:
        raise click.ClickException(f"unknown cadence {slug!r}")
    return dict(matched)


@cadence_group.command("get", cls=FleetCommand)
@click.argument("slug")
@project_option(required_by_hub=True)
def cadence_show(cli: CliContext, slug: str, project: str | None) -> None:
    """One cadence's whole record — its rhythm, its chores with their ids, and its attention."""
    body = _resolve_cadence(cli, slug, project)
    _flag_notice()
    cli.show(body, CadenceDetail(body))


@cadence_group.command("errands", cls=FleetCommand)
@click.argument("slug")
@project_option(required_by_hub=True)
def cadence_errands(cli: CliContext, slug: str, project: str | None) -> None:
    """Cadence SLUG's errands — each chore over each scope — with its state, its last run, and when it is next due.

    A due errand mints on the next pass; due_at is empty while an errand is running or stopped."""
    cadence = _resolve_cadence(cli, slug, project)
    rows = cli.get(
        f"/api/cadences/{cadence['cadence_id']}/errands",
        "GET /cadences/{id}/errands",
    ).json()
    _flag_notice()
    cli.show(rows, ErrandListing(rows))


def _patch(cli: CliContext, cadence: dict[str, Any], body: dict[str, Any], *, done: str) -> None:
    """PATCH ``body`` onto ``cadence`` on the revision it was read at."""
    resp = cli.send(
        "patch",
        f"/api/cadences/{cadence['cadence_id']}",
        json_body=body,
        if_match=int(cadence["revision"]),
        door=True,
    )
    refusal = refusal_of(resp)
    if refusal.type == REVISION_CONFLICT:
        raise click.ClickException(f"cadence edit refused: {refusal.message or 'conflict'}")
    cli.check(resp, "PATCH /cadences/{id}")
    cli.show_lines(resp.json(), done)


@cadence_group.command("edit", cls=FleetCommand)
@click.argument("slug")
@click.option("--name", "display_name", default=None, help="The cadence's display name.")
@click.option("--cron", default=None, help="A five-field cron expression; the stored zone is kept unless --zone.")
@click.option("--zone", default=None, help="The IANA time zone; the stored cron is kept unless --cron.")
@click.option("--no-schedule", is_flag=True, default=False, help="Clear the schedule.")
@click.option("--spacing", default=None, help="The least time between an errand's runs: a whole number and m, h, or d.")
@click.option("--anchor", type=_ANCHORS, default=None, help="What the spacing counts from.")
@click.option("--no-spacing", is_flag=True, default=False, help="Clear the spacing, and the anchor with it.")
@project_option(required_by_hub=True)
def cadence_edit(
    cli: CliContext,
    slug: str,
    display_name: str | None,
    cron: str | None,
    zone: str | None,
    no_schedule: bool,
    spacing: str | None,
    anchor: str | None,
    no_spacing: bool,
    project: str | None,
) -> None:
    """Change cadence SLUG's name, schedule, spacing, or anchor; only the options given change, and its slug never
    changes."""
    if no_schedule and (cron is not None or zone is not None):
        raise click.UsageError("--no-schedule contradicts --cron and --zone")
    if no_spacing and (spacing is not None or anchor is not None):
        raise click.UsageError("--no-spacing contradicts --spacing and --anchor")
    cadence = _resolve_cadence(cli, slug, project)
    body: dict[str, Any] = {
        k: v for k, v in {"name": display_name, "spacing": spacing, "anchor": anchor}.items() if v is not None
    }
    if cron is not None or zone is not None:
        stored: dict[str, str] = cadence.get("schedule") or {}
        body["schedule"] = _schedule(cron or stored.get("cron"), zone or stored.get("zone"))
    if no_schedule:
        body["schedule"] = None
    if no_spacing:
        body |= {"spacing": None, "anchor": None}
    if not body:
        raise click.UsageError("nothing to change — give a field to set or clear")
    _patch(cli, cadence, body, done=f"cadence {slug!r} updated")


@cadence_group.command("retire", cls=FleetCommand)
@click.argument("slug")
@click.option("--by", "by", default="operator", help="Who is retiring, recorded as the actor.")
@project_option(required_by_hub=True)
def cadence_retire(cli: CliContext, slug: str, by: str, project: str | None) -> None:
    """Retire cadence SLUG — a reversible brake: a retired cadence mints nothing."""
    _set_lifecycle(cli, slug, verb="retire", by=by, project=project)


@cadence_group.command("enable", cls=FleetCommand)
@click.argument("slug")
@click.option("--by", "by", default="operator", help="Who is re-enabling, recorded as the actor.")
@project_option(required_by_hub=True)
def cadence_enable(cli: CliContext, slug: str, by: str, project: str | None) -> None:
    """Re-enable a retired cadence SLUG."""
    _set_lifecycle(cli, slug, verb="enable", by=by, project=project)


def _set_lifecycle(cli: CliContext, slug: str, *, verb: str, by: str, project: str | None) -> None:
    cadence = _resolve_cadence(cli, slug, project)
    resp = cli.post(
        f"/api/cadences/{cadence['cadence_id']}/{verb}",
        f"POST /cadences/{{id}}/{verb}",
        json_body={"by": by},
        door=True,
    )
    body = resp.json()
    state = "retired" if body.get("retired") else "enabled"
    cli.show_lines(body, f"cadence {slug!r} is now {state}")


@cadence_group.group("chore")
def cadence_chore_group() -> None:
    """Add, edit, or remove a cadence's chores — each an edit of the cadence."""


def _documents(cadence: dict[str, Any]) -> list[dict[str, Any]]:
    """The cadence's chores as the edit request writes them back."""
    return [
        {
            "key": chore["key"],
            "routine": chore["routine"],
            "scopes": chore.get("scopes"),
            "mode": chore["mode"],
            "note": chore.get("note"),
            "promote": chore["promote"],
        }
        for chore in _chores(cadence)
    ]


def _chores(cadence: dict[str, Any]) -> list[dict[str, Any]]:
    return cadence.get("chores") or []


def _scope_selection(all_scopes: bool, scopes: tuple[str, ...], default_scope: bool) -> tuple[bool, Any]:
    """Whether a selection was given, and the ``scopes`` it writes."""
    given = [all_scopes, bool(scopes), default_scope]
    if sum(given) > 1:
        raise click.UsageError("give one of --all-scopes, --scope, and --default-scope")
    if all_scopes:
        return True, "all"
    if scopes:
        return True, list(scopes)
    return default_scope, None


@cadence_chore_group.command("add", cls=FleetCommand)
@click.argument("slug")
@click.argument("key")
@click.option("--routine", required=True, help="The routine the chore runs, by slug.")
@click.option("--scope", "scopes", multiple=True, help="Run over this scope slug; repeatable.")
@click.option("--all-scopes", is_flag=True, default=False, help="Run over every scope linked to the routine.")
@click.option("--mode", type=_MODES, default=None, help="The run mode; full when unstated.")
@click.option("--note", default=None, help="A note each run's charge carries.")
@click.option("--hold", is_flag=True, default=False, help="Leave each minted chunk not ready rather than promoting it.")
@project_option(required_by_hub=True)
def chore_add(
    cli: CliContext,
    slug: str,
    key: str,
    routine: str,
    scopes: tuple[str, ...],
    all_scopes: bool,
    mode: str | None,
    note: str | None,
    hold: bool,
    project: str | None,
) -> None:
    """Add chore KEY to cadence SLUG, running --routine over its default scope unless --scope or --all-scopes."""
    _, selection = _scope_selection(all_scopes, scopes, False)
    cadence = _resolve_cadence(cli, slug, project)
    chores = _documents(cadence)
    chore: dict[str, Any] = {"key": key, "routine": routine, "scopes": selection, "promote": not hold}
    chore |= {k: v for k, v in {"mode": mode, "note": note}.items() if v is not None}
    _patch(cli, cadence, {"chores": [*chores, chore]}, done=f"added chore {key!r} to cadence {slug!r}")


@cadence_chore_group.command("edit", cls=FleetCommand)
@click.argument("slug")
@click.argument("key")
@click.option("--routine", default=None, help="The routine the chore runs, by slug.")
@click.option("--scope", "scopes", multiple=True, help="Run over this scope slug; repeatable.")
@click.option("--all-scopes", is_flag=True, default=False, help="Run over every scope linked to the routine.")
@click.option("--default-scope", is_flag=True, default=False, help="Run over the routine's default scope.")
@click.option("--mode", type=_MODES, default=None, help="The run mode.")
@click.option("--note", default=None, help="A note each run's charge carries.")
@click.option("--hold/--promote", "hold", default=None, help="Leave each minted chunk not ready, or promote it.")
@project_option(required_by_hub=True)
def chore_edit(
    cli: CliContext,
    slug: str,
    key: str,
    routine: str | None,
    scopes: tuple[str, ...],
    all_scopes: bool,
    default_scope: bool,
    mode: str | None,
    note: str | None,
    hold: bool | None,
    project: str | None,
) -> None:
    """Change chore KEY of cadence SLUG; only the options given change, and the chore keeps its id."""
    selected, selection = _scope_selection(all_scopes, scopes, default_scope)
    cadence = _resolve_cadence(cli, slug, project)
    chores = _documents(cadence)
    chore = next((c for c in chores if c["key"] == key), None)
    if chore is None:
        raise click.ClickException(f"cadence {slug!r} has no chore {key!r}")
    changes: dict[str, Any] = {
        k: v for k, v in {"routine": routine, "mode": mode, "note": note}.items() if v is not None
    }
    if selected:
        changes["scopes"] = selection
    if hold is not None:
        changes["promote"] = not hold
    if not changes:
        raise click.UsageError("nothing to change — give a field to set")
    chore |= changes
    _patch(cli, cadence, {"chores": chores}, done=f"chore {key!r} of cadence {slug!r} updated")


@cadence_chore_group.command("remove", cls=FleetCommand)
@click.argument("slug")
@click.argument("key")
@project_option(required_by_hub=True)
def chore_remove(cli: CliContext, slug: str, key: str, project: str | None) -> None:
    """Remove chore KEY from cadence SLUG; adding the key back later mints a new chore."""
    cadence = _resolve_cadence(cli, slug, project)
    chores = _documents(cadence)
    kept = [c for c in chores if c["key"] != key]
    if len(kept) == len(chores):
        raise click.ClickException(f"cadence {slug!r} has no chore {key!r}")
    _patch(cli, cadence, {"chores": kept}, done=f"removed chore {key!r} from cadence {slug!r}")
