"""``blizzard hub repo`` (component tier) — a real ``build_hosted_app`` the commands' requests
are routed into, headers included, so the recorded door is the CLI's own."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from blizzard.hub.cli import hub as hub_group
from tests.config_inputs import build_hosted_hub_app, init_hub
from tests.worker_http import Bound, bind_stubs

pytestmark = pytest.mark.component

_HUB = "http://hub.local:8421"
_SENTINEL = "tok-planted-repo"
_COORDINATE = ["--forge-api-url", "https://forge.invalid/api", "--owner", "paul-gross", "--repo", "blizzard"]


_bound: dict[str, Bound] = {}
"""The collaborators the ``client`` fixture bound over its in-process app, for the invokers below."""


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    config = init_hub(tmp_path / "hub")
    with TestClient(build_hosted_hub_app(config)) as c:

        def forward(method: str, url: str, *, timeout: float, **kwargs):  # type: ignore[no-untyped-def]
            return c.request(method.upper(), urlsplit(url).path, **kwargs)

        _bound["hub"] = bind_stubs(request=forward)
        yield c


def _cli(group: str, args: list[str], *, stdin: str | None = None):  # type: ignore[no-untyped-def]
    return _bound["hub"].runner.invoke(hub_group, [group, *args], input=stdin, env={"BZ_HUB_URL": _HUB})


def _repo(args: list[str]):  # type: ignore[no-untyped-def]
    return _cli("repo", args)


def _seed() -> None:
    assert _cli("secret", ["create", "gh-test"], stdin=_SENTINEL).exit_code == 0
    created = _repo(["create", "blizzard", *_COORDINATE, "--base-branch", "master", "--secret", "gh-test"])
    assert created.exit_code == 0, created.output
    assert "revision 1" in created.output


def test_create_list_and_show_with_and_without_json(client: TestClient) -> None:
    _seed()

    listed = json.loads(_repo(["list", "--json"]).stdout)
    assert [(r["slug"], r["name"]) for r in listed] == [("blizzard", "blizzard")]
    assert (
        f"blizzard  blizzard  {listed[0]['repository_id']}  r1  enabled  paul-gross/blizzard  master"
        in _repo(["list"]).output
    )
    assert _repo(["get", listed[0]["repository_id"]]).exit_code == 0
    shown = _repo(["get", "blizzard"])
    assert "revision 1" in shown.output
    assert "secret gh-test" in shown.output
    assert json.loads(_repo(["get", "blizzard", "--json"]).stdout) == client.get("/api/repositories/blizzard").json()
    assert _repo(["get", "nope"]).exit_code != 0


def test_forge_cli_create_edit_show_and_lifecycle(client: TestClient) -> None:
    created = _cli("forge", ["create", "ghe", "--kind", "github", "--api-base", "https://ghe.example/api/v3"])
    assert created.exit_code == 0, created.output
    listed = json.loads(_cli("forge", ["list", "--json"]).stdout)
    assert [(r["slug"], r["web_origin"]) for r in listed] == [("ghe", "https://ghe.example")]
    assert json.loads(_cli("forge", ["get", "ghe", "--json"]).stdout) == client.get("/api/forges/ghe").json()
    assert "revision 2" in _cli("forge", ["edit", "ghe", "--name", "Enterprise", "--if-match", "1"]).output
    assert _cli("forge", ["edit", "ghe", "--name", "Enterprise", "--if-match", "1"]).exit_code != 0
    assert "retired" in _cli("forge", ["retire", "ghe"]).output
    assert json.loads(_cli("forge", ["list", "--json"]).stdout) == []
    assert "enabled" in _cli("forge", ["enable", "ghe"]).output


def test_create_refuses_a_taken_coordinate_and_a_missing_secret(client: TestClient) -> None:
    _seed()

    clash = _repo(["create", "other", *_COORDINATE, "--base-branch", "master", "--secret", "gh-test"])
    assert clash.exit_code != 0
    assert "repository blizzard" in clash.output
    missing = _repo(["create", "other", *_COORDINATE[:-1], "other", "--base-branch", "master", "--secret", "missing"])
    assert missing.exit_code != 0
    assert "secret_name" in missing.output


def test_edit_moves_the_revision_and_refuses_a_stale_if_match(client: TestClient) -> None:
    _seed()

    assert "revision 2" in _repo(["edit", "blizzard", "--base-branch", "main", "--if-match", "1"]).output
    stale = _repo(["edit", "blizzard", "--base-branch", "main", "--if-match", "1"])
    assert stale.exit_code != 0
    assert "revision 2" in stale.output
    assert _repo(["edit", "blizzard"]).exit_code != 0


def test_retire_enable_and_the_change_log(client: TestClient) -> None:
    _seed()
    _repo(["edit", "blizzard", "--base-branch", "main"])

    assert "retired" in _repo(["retire", "blizzard"]).output
    assert "blizzard" not in _repo(["list"]).output
    assert "blizzard" in _repo(["list", "--include-retired"]).output
    assert _cli("secret", ["retire", "gh-test"]).exit_code == 0
    refused = _repo(["enable", "blizzard"])
    assert refused.exit_code != 0
    assert "secret_name" in refused.output

    raw = json.loads(_cli("config", ["changes", "--json", "--kind", "repository", "--all"]).stdout)["changes"]
    assert [(c["op"], c["door"]) for c in raw] == [("retire", "cli"), ("edit", "cli"), ("create", "cli")]
    assert raw[1]["diff"] == [
        {"field": "base_branch", "old": "master", "new": "main", "old_label": None, "new_label": None}
    ]
    text = _cli("config", ["changes", "--kind", "repository"]).output
    assert "cli  repository blizzard  r1  create" in text
    assert _SENTINEL not in text + json.dumps(raw)


def test_create_names_its_forge_by_exactly_one_flag_and_warns_on_the_old_one(client: TestClient) -> None:
    assert _cli("secret", ["create", "gh-test"], stdin=_SENTINEL).exit_code == 0
    old = _repo(["create", "blizzard", *_COORDINATE, "--base-branch", "master", "--secret", "gh-test"])
    assert old.exit_code == 0, old.output
    assert "warning: forge_api_url is deprecated" in old.stderr
    assert "forge-invalid" in old.stderr

    named = ["--owner", "paul-gross", "--repo", "other", "--base-branch", "master", "--secret", "gh-test"]
    by_forge = _repo(["create", "other", "--forge", "forge-invalid", *named])
    assert by_forge.exit_code == 0, by_forge.output
    assert "warning:" not in by_forge.stderr
    assert client.get("/api/repositories/other").json()["forge_slug"] == "forge-invalid"
    assert "forge forge-invalid" in _repo(["get", "other"]).output

    both = _repo(
        ["create", "third", "--forge", "forge-invalid", "--forge-api-url", "https://forge.invalid/api", *named]
    )
    neither = _repo(["create", "third", *named])
    assert (both.exit_code, neither.exit_code) == (2, 2)
    assert "exactly one of --forge and --forge-api-url" in neither.output
