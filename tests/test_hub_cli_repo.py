"""``blizzard hub repo`` (component tier) — a real ``build_hosted_app`` the CLI's ``httpx``
verbs are routed into, headers included, so the recorded door is the CLI's own."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from blizzard.hub import app as hub_app
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.cli import hub as hub_group

pytestmark = pytest.mark.component

_HUB = "http://hub.local:8421"
_SENTINEL = "tok-planted-repo"
_COORDINATE = ["--forge-api-url", "https://forge.invalid/api", "--owner", "paul-gross", "--repo", "blizzard"]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    config = hub_runtime.init_environment(tmp_path / "hub")
    with TestClient(hub_app.build_hosted_app(config)) as c:

        def route(method: str):  # type: ignore[no-untyped-def]
            def call(url: str, *, timeout: float, **kwargs):  # type: ignore[no-untyped-def]
                return c.request(method.upper(), urlsplit(url).path, **kwargs)

            return call

        for method in ("get", "post", "put", "patch"):
            monkeypatch.setattr(httpx, method, route(method))
        yield c


def _cli(group: str, args: list[str], *, stdin: str | None = None):  # type: ignore[no-untyped-def]
    return CliRunner().invoke(hub_group, [group, *args], input=stdin, env={"BZ_HUB_URL": _HUB})


def _repo(args: list[str]):  # type: ignore[no-untyped-def]
    return _cli("repo", args)


def _seed() -> None:
    assert _cli("secret", ["create", "gh-test"], stdin=_SENTINEL).exit_code == 0
    created = _repo(["create", "blizzard", *_COORDINATE, "--base-branch", "master", "--secret", "gh-test"])
    assert created.exit_code == 0, created.output
    assert "revision 1" in created.output


def test_create_list_and_show_with_and_without_json(client: TestClient) -> None:
    _seed()

    assert "blizzard  r1  enabled  paul-gross/blizzard  master" in _repo(["list"]).output
    assert [r["name"] for r in json.loads(_repo(["list", "--json"]).output)] == ["blizzard"]
    shown = _repo(["show", "blizzard"])
    assert "revision 1" in shown.output
    assert "secret gh-test" in shown.output
    assert json.loads(_repo(["show", "blizzard", "--json"]).output) == client.get("/api/repositories/blizzard").json()
    assert _repo(["show", "nope"]).exit_code != 0


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

    raw = json.loads(_cli("config", ["changes", "--json", "--kind", "repository", "--all"]).output)["changes"]
    assert [(c["op"], c["door"]) for c in raw] == [("retire", "cli"), ("edit", "cli"), ("create", "cli")]
    assert raw[1]["diff"] == [{"field": "base_branch", "old": "master", "new": "main"}]
    text = _cli("config", ["changes", "--kind", "repository"]).output
    assert "cli  repository blizzard  r1  create" in text
    assert _SENTINEL not in text + json.dumps(raw)
