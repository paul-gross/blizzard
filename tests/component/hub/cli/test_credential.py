"""``blizzard hub credential`` and ``--credential`` on ``repo`` and ``source`` (component tier) — a real
``build_hosted_app`` the commands' requests are routed into, headers included, so the recorded door is the CLI's own."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from blizzard.hub.cli import hub as hub_group
from tests.support.config_inputs import build_hosted_hub_app, init_hub
from tests.support.worker_http import Bound, bind_stubs

_HUB = "http://hub.local:8421"
_SENTINEL = "tok-planted-credential"
_REPO = ["--forge", "forge-invalid", "--owner", "paul-gross", "--repo", "blizzard", "--base-branch", "master"]


_bound: dict[str, Bound] = {}
"""The collaborators the ``client`` fixture bound over its in-process app, for the invokers below."""


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    config = init_hub(tmp_path / "hub")
    with TestClient(build_hosted_hub_app(config)) as c:

        def forward(method: str, url: str, *, timeout: float, **kwargs: Any):
            return c.request(method.upper(), urlsplit(url).path, **kwargs)

        _bound["hub"] = bind_stubs(request=forward)
        yield c


def _cli(group: str, args: list[str], *, stdin: str | None = None):
    return _bound["hub"].runner.invoke(hub_group, [group, *args], input=stdin, env={"BZ_HUB_URL": _HUB})


def _cred(args: list[str]):
    return _cli("credential", args)


def _seed() -> None:
    forge = _cli("forge", ["create", "forge-invalid", "--kind", "github", "--api-base", "https://forge.invalid/api"])
    assert forge.exit_code == 0, forge.output
    assert _cli("secret", ["create", "gh-test"], stdin=_SENTINEL).exit_code == 0
    created = _cred(["create", "gh", "--forge", "forge-invalid", "--binding", "github.token", "--secret", "gh-test"])
    assert created.exit_code == 0, created.output
    assert "revision 1" in created.output


def test_create_list_and_get_with_and_without_json(client: TestClient) -> None:
    _seed()

    listed = json.loads(_cred(["list", "--json"]).stdout)
    assert [(r["slug"], r["binding"], r["secret_name"]) for r in listed] == [("gh", "github.token", "gh-test")]
    assert f"gh  gh  {listed[0]['credential_id']}  r1  enabled  forge-invalid  github.token" in _cred(["list"]).output
    shown = _cred(["get", "gh"])
    assert shown.exit_code == 0, shown.output
    assert "binding github.token  secret gh-test" in shown.output
    assert json.loads(_cred(["get", "gh", "--json"]).stdout) == client.get("/api/credentials/gh").json()
    assert _cred(["show", listed[0]["credential_id"]]).exit_code == 0
    assert _cred(["read", "gh"]).exit_code == 0
    assert _cred(["get", "nope"]).exit_code != 0
    assert _SENTINEL not in shown.output


def test_create_refuses_a_taken_slug_and_a_missing_secret(client: TestClient) -> None:
    _seed()

    taken = _cred(["create", "gh", "--forge", "forge-invalid", "--binding", "github.token", "--secret", "gh-test"])
    missing = _cred(["create", "other", "--forge", "forge-invalid", "--binding", "github.token", "--secret", "nope"])

    assert taken.exit_code != 0
    assert missing.exit_code != 0
    assert [r["slug"] for r in client.get("/api/credentials").json()["credentials"]] == ["gh"]


def test_edit_moves_the_revision_sets_the_variant_and_refuses_a_stale_if_match(client: TestClient) -> None:
    _seed()

    edited = _cred(["edit", "gh", "--name", "GitHub", "--variant", "fine-grained", "--if-match", "1"])
    stale = _cred(["edit", "gh", "--name", "Other", "--if-match", "1"])

    assert "revision 2" in edited.output, edited.output
    assert client.get("/api/credentials/gh").json()["settings"] == {"variant": "fine-grained"}
    assert stale.exit_code != 0
    assert _cred(["edit", "gh"]).exit_code != 0


def test_create_and_edit_an_app_installation_credential(client: TestClient) -> None:
    _seed()
    assert _cli("secret", ["create", "app-key"], stdin="-----BEGIN PRIVATE KEY-----\nkey\n").exit_code == 0
    app = ["--forge", "forge-invalid", "--binding", "github.app-installation", "--secret", "app-key"]

    created = _cred(["create", "app", *app, "--app-id", "12", "--installation-id", "34"])
    half = _cred(["edit", "app", "--installation-id", "56"])
    edited = _cred(["edit", "app", "--app-id", "12", "--installation-id", "56"])

    assert created.exit_code == 0, created.output
    assert half.exit_code != 0
    assert "app_id" in half.output
    assert edited.exit_code == 0, edited.output
    shown = json.loads(_cred(["get", "app", "--json"]).stdout)
    assert (shown["settings"], shown["authority"], shown["revision"]) == (
        {"app_id": 12, "installation_id": 56},
        "app/12/installation/56",
        2,
    )
    assert "setting installation_id 56" in _cred(["get", "app"]).output


def test_retire_refuses_while_named_then_retires_and_enables(client: TestClient) -> None:
    _seed()
    repo = _cli("repo", ["create", "blizzard", *_REPO, "--credential", "gh"])
    assert repo.exit_code == 0, repo.output
    assert "warning" not in repo.stderr

    refused = _cred(["retire", "gh"])
    assert refused.exit_code != 0
    assert "repository" in refused.output
    assert "repository blizzard" in _cred(["get", "gh"]).output

    assert _cli("repo", ["retire", "blizzard"]).exit_code == 0
    assert "retired" in _cred(["retire", "gh"]).output
    assert _cred(["list", "--json"]).stdout.strip() == "[]"
    assert "enabled" in _cred(["enable", "gh"]).output

    raw = json.loads(_cli("config", ["changes", "--json", "--kind", "credential", "--all"]).stdout)["changes"]
    assert [(c["op"], c["door"]) for c in raw] == [("enable", "cli"), ("retire", "cli"), ("create", "cli")]


def test_repo_names_its_credential_and_an_old_secret_warns_on_stderr(client: TestClient) -> None:
    _seed()

    named = _cli("repo", ["create", "blizzard", *_REPO, "--credential", "gh"])
    old = _cli("repo", ["create", "other", *_REPO[:5], "other", *_REPO[6:], "--secret", "gh-test", "--json"])
    neither = _cli("repo", ["create", "third", *_REPO[:5], "third", *_REPO[6:]])

    assert named.exit_code == 0, named.output
    assert "credential gh" in _cli("repo", ["get", "blizzard"]).output
    assert old.exit_code == 0, old.output
    assert "warning: " in old.stderr
    assert json.loads(old.stdout)["credential_slug"] == "gh"
    assert neither.exit_code != 0
    assert client.get("/api/repositories/third").status_code == 404


def test_repo_and_source_edit_take_a_credential(client: TestClient) -> None:
    _seed()
    assert _cli("repo", ["create", "blizzard", *_REPO, "--secret", "gh-test"]).exit_code == 0
    source = _cli(
        "source",
        [
            "create",
            "issues",
            "--provider",
            "github",
            "--locator",
            "paul-gross/blizzard",
            "--forge",
            "forge-invalid",
            "--credential",
            "gh",
        ],
    )
    assert source.exit_code == 0, source.output
    assert "warning" not in source.stderr
    assert "credential gh" in _cli("source", ["get", "issues"]).output

    edited = _cli("repo", ["edit", "blizzard", "--credential", "gh", "--base-branch", "main"])
    source_edit = _cli("source", ["edit", "issues", "--credential", "gh", "--name", "Issues"])

    assert edited.exit_code == 0, edited.output
    assert source_edit.exit_code == 0, source_edit.output
    assert client.get("/api/work-sources/issues").json()["credential_slug"] == "gh"
