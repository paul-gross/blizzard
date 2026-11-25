"""``blizzard hub credential`` and ``--credential`` on ``repo`` and ``source`` (component tier) — a real
``build_hosted_app`` the commands' requests are routed into, headers included, so the recorded door is the CLI's own."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx2
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
    assert listed == client.get("/api/credentials").json()["credentials"]
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


def _operation(operation: str, outcome: str, detail: str | None = None) -> dict[str, Any]:
    return {"operation": operation, "outcome": outcome, "detail": detail, "diagnostic": None}


def _check(
    *,
    mint: dict[str, Any] | None = None,
    outcome: str = "proven",
    reached: bool = True,
    unreached_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "credential": "gh",
        "binding": "github.token",
        "variant": "classic",
        "variant_contradicted": False,
        "mint": mint,
        "granted_permissions": None,
        "uses": [
            {
                "kind": "repository",
                "id": "repo-1",
                "slug": "widget",
                "operations": [_operation("access", "proven"), _operation("refs.read", outcome, "said why")],
                "reached": reached,
            }
        ],
        "unreached_reason": unreached_reason,
        "not_evaluated": "Branch protection, required reviews, and rulesets are not evaluated.",
    }


class _CheckHub:
    """A hub answering every check with ``answer``, recording each request's body and client timeout."""

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer
        self.sent: list[tuple[str, object, float]] = []
        _bound["hub"] = bind_stubs(request=self.forward)

    def forward(self, method: str, url: str, *, timeout: float, **kwargs: Any) -> httpx2.Response:
        self.sent.append((f"{method.upper()} {urlsplit(url).path}", kwargs.get("json"), timeout))
        return httpx2.Response(200, json=self.answer, request=httpx2.Request(method.upper(), url))


def test_check_prints_each_use_s_table_and_exits_0_when_nothing_is_refused() -> None:
    hub = _CheckHub(_check())

    result = _cred(["check", "gh"])

    assert result.exit_code == 0, result.output
    assert hub.sent == [("POST /api/credentials/gh/checks", {}, 75.0)]
    assert result.output.splitlines() == [
        "credential gh  github.token  classic",
        "repository widget  repo-1",
        "  access  proven",
        "  refs.read  proven  said why",
        "Branch protection, required reviews, and rulesets are not evaluated.",
    ]


@pytest.mark.parametrize("outcome", ["refused", "unsupported"])
def test_check_exits_1_on_a_refused_or_unsupported_operation(outcome: str) -> None:
    _CheckHub(_check(outcome=outcome))

    assert _cred(["check", "gh"]).exit_code == 1


@pytest.mark.parametrize("outcome", ["granted", "unproven"])
def test_check_exits_0_on_a_granted_or_unproven_write(outcome: str) -> None:
    _CheckHub(_check(outcome=outcome))

    assert _cred(["check", "gh"]).exit_code == 0


def test_check_exits_1_on_a_refused_mint_and_prints_it() -> None:
    _CheckHub(_check(mint=_operation("mint", "refused", "The installation is gone.")))

    result = _cred(["check", "gh"])

    assert result.exit_code == 1
    assert "mint  refused  The installation is gone." in result.output.splitlines()


def test_check_posts_its_repositories_and_sources_and_marks_an_unreached_use() -> None:
    hub = _CheckHub(_check(reached=False))

    result = _cred(["check", "gh", "--repo", "widget", "--repo", "gadget", "--source", "issues"])

    assert hub.sent[0][1] == {"repositories": ["widget", "gadget"], "work_sources": ["issues"]}
    assert "repository widget  repo-1  (not reached)" in result.output.splitlines()
    assert json.loads(_cred(["check", "gh", "--json"]).stdout) == _check(reached=False)


def test_check_exits_1_and_says_why_when_it_reached_no_use() -> None:
    reason = "the installation could not be read: ReadTimeout"
    _CheckHub(_check(reached=False, unreached_reason=reason))

    result = _cred(["check", "gh"])

    assert result.exit_code == 1
    assert f"no use reached: {reason}" in result.output.splitlines()
