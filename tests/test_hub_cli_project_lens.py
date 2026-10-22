"""``--project`` on the hub's record and listing verbs, against an in-process hub whose tenant holds two projects:
a record goes to the project named by slug or id, a create or by-name verb naming none prints the tenant's projects
and fails, and a listing names a project to narrow to it or none to read the whole tenant."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import click
import pytest
from click.testing import Result

from blizzard.hub.cli import hub as hub_group
from tests.support import HubHarness, build_hub, default_project, ingest, link_project, seed_project
from tests.worker_http import Bound, bind_stubs

pytestmark = pytest.mark.component

_HUB_URL = "http://testserver"
_REFUSAL = "name the one to act in with --project <id|slug>"


@dataclass(frozen=True)
class Call:
    method: str
    path: str
    query: dict[str, list[str]]


@dataclass(frozen=True)
class Tenant:
    """The hub, its ``default`` project and a second one, ``gears``, both by id."""

    hub: HubHarness
    default: str
    gears: str


@pytest.fixture
def tenant(tmp_path: Path) -> Tenant:
    hub = build_hub(tmp_path)
    default = default_project(hub.engine)
    return Tenant(hub, default, seed_project(hub.engine, "gears"))


_bound: dict[str, Bound] = {}
"""The collaborators the routing fixture bound over the in-process hub, for the invokers below."""


@pytest.fixture
def calls(tenant: Tenant) -> list[Call]:
    """Route every call the CLI makes to the tenant's hub, and hand back each one's method, path and query."""
    made: list[Call] = []

    def forward(method: str, url: str, *, timeout: float, **kwargs: Any) -> Any:
        response = tenant.hub.client.request(method, url, **kwargs)
        sent = urlsplit(str(response.request.url))
        made.append(Call(method, sent.path, parse_qs(sent.query)))
        return response

    _bound["hub"] = bind_stubs(request=forward)
    return made


def _cli(*argv: str, stdin: str | None = None) -> Result:
    return _bound["hub"].runner.invoke(hub_group, [*argv, "--hub-url", _HUB_URL], input=stdin)


def _said(result: Result) -> list[str]:
    """The CLI's own lines, without the hub's log lines that share the captured stream."""
    return [line for line in result.output.splitlines() if '"event": ' not in line]


def _json(result: Result) -> Any:
    assert result.exit_code == 0, result.output
    return json.loads(_said(result)[-1])


def _promote(tenant: Tenant, chunk_id: str) -> None:
    resp = tenant.hub.client.post(f"/api/chunks/{chunk_id}/promote")
    assert resp.status_code == 202, resp.text


def _item_chunk(project: str) -> str:
    """A hub item authored in ``project`` through ``item create``, and the chunk it minted."""
    return _json(_cli("item", "create", "--title", "t", "--body-file", "-", "--project", project, "--json", stdin="b"))[
        "chunk_id"
    ]


@pytest.mark.usefixtures("calls")
def test_a_record_create_lands_in_the_project_named_by_slug_or_by_id(tenant: Tenant) -> None:
    by_slug = _json(_cli("scope", "create", "billing", "--project", "gears", "--json"))
    by_id = _json(_cli("scope", "create", "audits", "--project", tenant.default, "--json"))
    proposal = _json(
        _cli(
            "garden-proposal",
            "create",
            "--title",
            "tidy",
            "--class",
            "chore",
            "--body-file",
            "-",
            "--project",
            "gears",
            "--json",
            stdin="body",
        )
    )

    assert (by_slug["project_id"], by_id["project_id"]) == (tenant.gears, tenant.default)
    assert proposal["project_id"] == tenant.gears
    assert _json(_cli("scope", "show", "billing", "--project", tenant.gears, "--json"))["slug"] == "billing"
    edited = _cli("scope", "edit", "billing", "--description", "bills", "--project", "gears")
    assert edited.exit_code == 0, edited.output
    retired = _cli("scope", "retire", "billing", "--project", "gears")
    assert _said(retired) == ["scope billing is now retired"]
    findings = _cli("finding", "list", "--scope", "billing", "--source", "review", "--project", "gears")
    assert findings.exit_code == 0, findings.output


@pytest.mark.parametrize(
    ("argv", "stdin"),
    [
        (("scope", "create", "billing"), None),
        (("scope", "show", "billing"), None),
        (("routine", "create", "nightly", "alpha", "billing"), None),
        (("routine", "retire", "nightly"), None),
        (("garden-proposal", "create", "--title", "tidy", "--class", "chore", "--body-file", "-"), "body"),
        (("item", "create", "--title", "t", "--body-file", "-"), "body"),
        (("finding", "list", "--scope", "billing", "--source", "review"), None),
    ],
)
def test_a_verb_needing_a_project_named_none_prints_the_tenants_projects_and_fails(
    tenant: Tenant, calls: list[Call], argv: tuple[str, ...], stdin: str | None
) -> None:
    result = _cli(*argv, stdin=stdin)

    assert result.exit_code != 0
    said = _said(result)
    assert _REFUSAL in said[0]
    assert {line.split()[0] for line in said[2:] if line.startswith("  ")} == {tenant.default, tenant.gears}
    assert f"  {tenant.gears}  gears  gears" in said


@pytest.mark.usefixtures("calls")
def test_a_listing_without_project_reads_the_whole_tenant_and_with_it_only_that_project(tenant: Tenant) -> None:
    in_default = ingest(tenant.hub, [{"source": "default", "ref": "1"}])
    in_gears = _item_chunk("gears")
    _promote(tenant, in_gears)

    def chunk_ids(*argv: str) -> set[str]:
        return {row["chunk_id"] for row in _json(_cli(*argv, "--json"))}

    assert chunk_ids("chunk", "list") == {in_default, in_gears}
    assert chunk_ids("chunk", "list", "--project", "gears") == {in_gears}
    assert chunk_ids("chunk", "list", "--project", tenant.default) == {in_default}
    queued = _json(_cli("queue", "show", "--json"))["entries"]
    assert {entry["chunk_id"] for entry in queued} == {in_default, in_gears}
    narrowed = _json(_cli("queue", "show", "--project", "gears", "--json"))["entries"]
    assert [entry["chunk_id"] for entry in narrowed] == [in_gears]
    assert {chunk["chunk_id"] for chunk in _json(_cli("status", "--project", "gears", "--json"))["chunks"]} == {
        in_gears
    }
    assert len(_json(_cli("status", "--json"))["chunks"]) == 2


_SINCE = ("--since", "2026-01-01T00:00:00", "--until", "2030-01-01T00:00:00")


@pytest.mark.parametrize(
    "argv",
    [
        ("scope", "list"),
        ("routine", "list"),
        ("routine", "proposal-counts", *_SINCE),
        ("garden-proposal", "list"),
        ("run", "list"),
        ("events",),
        ("analytics", "summary", "counts-files"),
        ("analytics", "events"),
        ("config", "changes"),
        ("chunk", "list"),
        ("queue", "show"),
        ("question", "list"),
        ("decision", "list"),
    ],
)
def test_a_listing_verb_sends_the_project_it_names_for_the_hub_to_narrow_by(
    calls: list[Call], argv: tuple[str, ...]
) -> None:
    named = _cli(*argv, "--project", "gears")
    sent_named = [call.query.get("project") for call in calls]
    calls.clear()
    unnamed = _cli(*argv)

    assert named.exit_code == 0, named.output
    assert unnamed.exit_code == 0, unnamed.output
    assert sent_named and all(project == ["gears"] for project in sent_named)
    assert [call.query.get("project") for call in calls] == [None] * len(calls)


def test_ingest_takes_the_named_project_and_names_a_source_that_does_not_serve_it(
    tenant: Tenant, calls: list[Call]
) -> None:
    unlinked = _cli("chunk", "ingest", "default:5", "--project", "gears")
    link_project(tenant.hub.engine, project=tenant.gears, source="default")
    ingested = _json(_cli("chunk", "ingest", "default:6", "--project", "gears", "--json"))
    ambiguous = _cli("chunk", "ingest", "default:7")

    assert unlinked.exit_code != 0
    assert f"source default is not linked to project {tenant.gears}" in unlinked.output
    assert ingested["project_id"] == tenant.gears
    assert [call.path for call in calls if call.method == "post"] == ["/api/chunks"] * 3
    assert ambiguous.exit_code != 0
    assert _REFUSAL in _said(ambiguous)[0]


@pytest.mark.usefixtures("calls")
def test_runner_add_declares_each_project_the_runner_serves(tenant: Tenant) -> None:
    added = _json(_cli("runner", "add", "r1", "--project", "gears", "--project", tenant.default, "--json"))
    plain = _json(_cli("runner", "add", "r2", "--json"))

    served = tenant.hub.client.get(f"/api/runners/{added['runner_id']}").json()["projects"]
    assert [(entry["entry"], entry["project_id"]) for entry in served] == [
        ("gears", tenant.gears),
        (tenant.default, tenant.default),
    ]
    assert tenant.hub.client.get(f"/api/runners/{plain['runner_id']}").json()["projects"] == []


@pytest.mark.parametrize("verb", ["retire", "enable", "run"])
def test_a_routine_named_without_a_project_on_a_tenant_holding_several_is_not_resolved(
    calls: list[Call], verb: str
) -> None:
    result = _cli("routine", verb, "nightly")

    assert result.exit_code != 0
    assert _REFUSAL in _said(result)[0]
    assert [call.path for call in calls] == ["/api/projects"]


def _command(*path: str) -> click.Command:
    command: click.Command | None = hub_group
    for name in path:
        assert isinstance(command, click.Group)
        command = command.get_command(click.Context(command), name)
    assert command is not None
    return command


@pytest.mark.parametrize(
    ("path", "says"),
    [
        (("scope", "create"), "The project the record belongs to"),
        (("routine", "create"), "The project the record belongs to"),
        (("garden-proposal", "create"), "The project the record belongs to"),
        (("item", "create"), "The project the record belongs to"),
        (("scope", "list"), "Show only this project's records"),
        (("chunk", "list"), "Show only this project's records"),
        (("run", "list"), "Show only this project's records"),
        (("question", "list"), "Show only this project's records"),
        (("decision", "list"), "Show only this project's records"),
        (("analytics", "summary"), "Show only this project's records"),
        (("status",), "Show only this project's chunks"),
        (("chunk", "ingest"), "The project to mint the chunk in"),
        (("runner", "add"), "A project the runner serves"),
    ],
)
def test_each_verbs_project_option_states_what_naming_a_project_does(path: tuple[str, ...], says: str) -> None:
    option = next(p for p in _command(*path).params if isinstance(p, click.Option) and "--project" in p.opts)

    assert not option.required
    assert option.help is not None
    assert option.help.startswith(says)
    assert "/api/" not in option.help
