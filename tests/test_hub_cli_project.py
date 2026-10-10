"""``blizzard hub project create|list|show|edit|link|unlink`` against a real hub: a project is named by its id, its
slug or a slug it left; its work-source and repository links are linked, shown and unlinked, each change carrying the
CLI's door; a call that needs a project and names none, on a tenant holding several, prints the tenant's projects and
asks for ``--project``; ``--tenant`` names the tenant a project verb acts in; and ``--project`` is offered optional
on the command line, its help stating what it does."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click
import httpx
import pytest
from click.testing import CliRunner, Result
from fastapi.testclient import TestClient

from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.cli import hub as hub_group
from blizzard.hub.cli.project_option import project_option, project_query
from tests.support import HubHarness, build_hub, default_project, seed_project

pytestmark = pytest.mark.component

_HUB_URL = "http://testserver"

Cli = Callable[..., Result]


def _ok(response: httpx.Response, status: int = 201) -> None:
    assert response.status_code == status, response.text


def _seed_records(client: TestClient) -> None:
    """A secret, the work source ``acme`` and the repository ``acme-repo`` that name it."""
    _ok(client.post("/api/secrets", json={"name": "token", "value": "v"}))
    _ok(
        client.post(
            "/api/work-sources", json={"name": "acme", "provider": "github", "locator": "a/w", "secret": "token"}
        )
    )
    repository = {
        "name": "acme-repo",
        "forge_api_url": "https://forge.example",
        "owner": "acme",
        "repo": "widgets",
        "base_branch": "main",
        "secret_name": "token",
    }
    _ok(client.post("/api/repositories", json=repository))


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path, link=False)
    _seed_records(hub.client)
    return hub


@pytest.fixture
def sent(hub: HubHarness, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """Route every call the CLI makes to ``hub``, and hand back the headers each one sent, in order."""
    headers_sent: list[dict[str, str]] = []

    def routed(method: str) -> Callable[..., Any]:
        def call(url: str, *, timeout: float, headers: dict[str, str] | None = None, **kwargs: Any) -> Any:
            headers_sent.append(dict(headers or {}))
            return hub.client.request(method, url, headers=headers, **kwargs)

        return call

    for method in ("get", "post", "put", "patch", "delete"):
        monkeypatch.setattr(httpx, method, routed(method))
    return headers_sent


def _cli(*argv: str) -> Result:
    return CliRunner().invoke(hub_group, ["project", *argv, "--hub-url", _HUB_URL])


def _said(result: Result) -> list[str]:
    """The CLI's own lines, without the hub's log lines that share the captured stream."""
    return [line for line in result.output.splitlines() if '"event": ' not in line]


def _json(result: Result) -> Any:
    assert result.exit_code == 0, result.output
    return json.loads(_said(result)[-1])


def _changes(hub: HubHarness, kind: str) -> list[dict[str, Any]]:
    resp = hub.client.get("/api/config/changes", params={"record_kind": kind})
    _ok(resp, 200)
    return resp.json()["changes"]


@pytest.mark.usefixtures("sent")
def test_create_names_the_project_by_its_slug_unless_given_a_name_and_list_and_show_read_it(hub: HubHarness) -> None:
    created = _cli("create", "widgets", "--description", "the widget line")
    named = _json(_cli("create", "gears", "--name", "Gear works", "--json"))

    assert created.exit_code == 0, created.output
    assert _said(created)[0].startswith("project widgets created at revision 1 — id ")
    widgets = _json(_cli("show", "widgets", "--json"))
    assert (widgets["slug"], widgets["name"], widgets["description"]) == ("widgets", "widgets", "the widget line")
    assert (named["slug"], named["name"]) == ("gears", "Gear works")
    listed = _said(_cli("list"))
    assert f"gears  Gear works  {named['project_id']}  r1" in listed
    assert f"widgets  widgets  {widgets['project_id']}  r1" in listed
    assert {row["slug"] for row in _json(_cli("list", "--json"))} == {"default", "gears", "widgets"}
    by_id = _said(_cli("show", named["project_id"]))
    assert by_id[:3] == [
        "gears  Gear works  revision 1",
        f"id {named['project_id']}  created {named['created_at']}",
        "description -",
    ]
    assert [change["door"] for change in _changes(hub, "project") if change["record_key"] == named["project_id"]] == [
        "cli"
    ]


@pytest.mark.usefixtures("sent")
def test_create_and_show_refusals_name_what_was_refused() -> None:
    assert _cli("create", "widgets").exit_code == 0

    taken = _cli("create", "widgets")
    malformed = _cli("create", "Not A Slug")
    unknown = _cli("show", "nowhere")

    assert taken.exit_code != 0
    assert "widgets" in taken.output
    assert malformed.exit_code != 0
    assert "slug" in malformed.output
    assert unknown.exit_code != 0


@pytest.mark.usefixtures("sent")
def test_edit_changes_only_the_fields_given_and_the_slug_left_still_names_the_project() -> None:
    project_id = _json(_cli("create", "widgets", "--json"))["project_id"]

    edited = _cli("edit", "widgets", "--slug", "gadgets", "--name", "Gadgets")
    by_former_slug = _json(_cli("show", "widgets", "--json"))
    stale = _cli("edit", project_id, "--description", "late", "--if-match", "1")
    nothing = _cli("edit", "gadgets")

    assert edited.exit_code == 0, edited.output
    assert _said(edited) == ["project gadgets is at revision 2"]
    assert (by_former_slug["project_id"], by_former_slug["name"], by_former_slug["description"]) == (
        project_id,
        "Gadgets",
        "",
    )
    assert stale.exit_code != 0
    assert "revision" in stale.output
    assert nothing.exit_code != 0
    assert "nothing to change" in nothing.output


@pytest.mark.usefixtures("sent")
def test_link_and_unlink_a_work_source_and_a_repository_by_slug_and_by_id(hub: HubHarness) -> None:
    project_id = _json(_cli("create", "widgets", "--json"))["project_id"]

    source = _cli("link", "widgets", "--source", "acme")
    repository = _cli("link", project_id, "--repository", "acme-repo")
    shown = _said(_cli("show", "widgets"))
    unlinked_source = _cli("unlink", project_id, "--source", "acme")
    unlinked_repository = _json(_cli("unlink", "widgets", "--repository", "acme-repo", "--json"))
    after = _json(_cli("show", "widgets", "--json"))

    assert _said(source) == ["project widgets is linked to work source acme"]
    assert _said(repository) == [f"project {project_id} is linked to repository acme-repo"]
    assert shown[-2:] == ["sources acme, hub (built in)", "repositories acme-repo"]
    assert _said(unlinked_source) == [f"project {project_id} is unlinked from work source acme"]
    assert (unlinked_repository["name"], unlinked_repository["linked"]) == ("acme-repo", False)
    assert [row["name"] for row in after["source_links"]] == ["hub"]
    assert after["repository_links"] == []
    # The build links what it seeds to the default project; only this project's link changes are the CLI's.
    link_changes = _changes(hub, "project_source_link") + _changes(hub, "project_repository_link")
    assert {(c["op"], c["door"]) for c in link_changes if c["project_id"] == project_id} == {
        ("create", "cli"),
        ("retire", "cli"),
    }


@pytest.mark.usefixtures("sent")
@pytest.mark.parametrize(
    ("argv", "said"),
    [
        (("link", "widgets"), "exactly one of --source or --repository"),
        (("unlink", "widgets", "--source", "acme", "--repository", "acme-repo"), "exactly one of"),
        (("unlink", "widgets", "--source", "hub"), "built in"),
        (("unlink", "widgets", "--repository", "acme-repo"), "acme-repo"),
        (("link", "widgets", "--source", "nowhere"), "nowhere"),
        (("link", "nowhere", "--repository", "acme-repo"), "nowhere"),
    ],
)
def test_a_link_verb_refuses_a_target_it_cannot_link_or_unlink(argv: tuple[str, ...], said: str) -> None:
    assert _cli("create", "widgets").exit_code == 0

    result = _cli(*argv)

    assert result.exit_code != 0
    assert said in result.output


def test_a_call_needing_a_project_on_a_tenant_holding_several_prints_its_projects(
    hub: HubHarness, sent: list[dict[str, str]]
) -> None:
    only = default_project(hub.engine)
    second = seed_project(hub.engine, "gears")

    result = CliRunner().invoke(hub_group, ["scope", "create", "billing", "--hub-url", _HUB_URL])

    assert result.exit_code != 0
    said = _said(result)
    assert "name the one to act in with --project <id|slug>" in said[0]
    assert {line.split()[0] for line in said[2:] if line.startswith("  ")} == {only, second}
    assert f"  {second}  gears  gears" in said


def test_tenant_names_the_tenant_a_project_verb_acts_in(hub: HubHarness, sent: list[dict[str, str]]) -> None:
    bravo = hub.add_tenant("bravo")

    created = _cli("create", "widgets", "--tenant", bravo.tenant)
    in_bravo = _json(_cli("list", "--json", "--tenant", bravo.tenant))

    assert created.exit_code == 0, created.output
    assert {row["slug"] for row in in_bravo} == {"default", "widgets"}
    assert [p.slug for p in hub.services.projects.list_all()] == ["default"]
    assert sent and all(headers[TENANT_HEADER] == bravo.tenant for headers in sent)


@pytest.mark.parametrize(
    ("verb", "effect"),
    [
        ("create", "Create project SLUG at revision 1"),
        ("list", "List the tenant's projects"),
        ("show", "with the work sources and repositories"),
        ("edit", "Change only the fields given on PROJECT"),
        ("link", "work ingested from that source can belong to"),
        ("unlink", "no longer goes to PROJECT"),
    ],
)
def test_each_project_verbs_help_states_its_effect(verb: str, effect: str) -> None:
    result = CliRunner().invoke(hub_group, ["project", verb, "--help"])

    assert result.exit_code == 0, result.output
    assert effect in " ".join(result.output.split())
    assert "/api/" not in result.output


@pytest.mark.parametrize(("required_by_hub", "says"), [(True, "Needed only when"), (False, "Show only this project")])
def test_project_option_is_optional_on_the_command_line_and_says_what_it_does(required_by_hub: bool, says: str) -> None:
    @click.command()
    @project_option(required_by_hub=required_by_hub)
    def verb(project: str | None) -> None:
        click.echo(json.dumps(project_query(project)))

    named = CliRunner().invoke(verb, ["--project", "gears"])
    unnamed = CliRunner().invoke(verb, [])
    shown = CliRunner().invoke(verb, ["--help"])

    assert json.loads(named.output) == {"project": "gears"}
    assert json.loads(unnamed.output) == {}
    assert says in " ".join(shown.output.split())
