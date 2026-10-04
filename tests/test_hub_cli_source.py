"""``blizzard hub source`` and ``blizzard hub config changes`` (component tier) — a real
``build_hosted_app`` the CLI's ``httpx`` verbs are routed into, headers included, so the
recorded door is the CLI's own."""

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
_SENTINEL = "tok-planted-840"


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


def _source(args: list[str]):  # type: ignore[no-untyped-def]
    return _cli("source", args)


def _seed(client: TestClient) -> None:
    assert _cli("secret", ["set", "gh-demo"], stdin=_SENTINEL).exit_code == 0
    created = _source(["create", "demo", "--provider", "github", "--locator", "acme/demo", "--secret", "gh-demo"])
    assert created.exit_code == 0, created.output


def test_create_list_and_show_with_and_without_json(client: TestClient) -> None:
    _seed(client)

    listed = _source(["list"])
    assert "hub  built-in" in listed.output
    assert "demo  r1  enabled  github  acme/demo" in listed.output
    rows = json.loads(_source(["list", "--json"]).output)
    assert {r["name"] for r in rows} == {"hub", "demo"}
    shown = _source(["show", "demo"])
    assert "revision 1" in shown.output
    assert "secret gh-demo" in shown.output
    assert json.loads(_source(["show", "demo", "--json"]).output)["revision"] == 1
    assert _source(["show", "nope"]).exit_code != 0


def test_edit_sets_clears_and_refuses_a_stale_if_match(client: TestClient) -> None:
    _seed(client)

    assert "revision 2" in _source(["edit", "demo", "--api-base", "https://ghe.example/api/v3"]).output
    assert "revision 3" in _source(["edit", "demo", "--clear", "api-base"]).output
    assert json.loads(_source(["show", "demo", "--json"]).output)["api_base"] is None
    stale = _source(["edit", "demo", "--if-match", "1", "--no-annotate"])
    assert stale.exit_code != 0
    assert "revision 3" in stale.output
    assert _source(["edit", "demo"]).exit_code != 0
    assert _source(["edit", "demo", "--api-base", "x", "--clear", "api-base"]).exit_code != 0
    refused = _source(["edit", "demo", "--clear", "secret"])
    assert refused.exit_code != 0
    assert "secret" in refused.output
    assert _source(["edit", "hub", "--annotate"]).exit_code != 0


def test_retire_and_enable_and_the_secret_reference_guard(client: TestClient) -> None:
    _seed(client)

    refused = _cli("secret", ["retire", "gh-demo"])
    assert refused.exit_code != 0
    assert "work_source demo" in refused.output
    assert "work_source demo" in _cli("secret", ["show", "gh-demo"]).output
    assert json.loads(_cli("secret", ["show", "gh-demo", "--json"]).output)["references"] == [
        {"kind": "work_source", "key": "demo"}
    ]

    assert "retired" in _source(["retire", "demo"]).output
    assert "demo" not in _source(["list"]).output
    assert "demo" in _source(["list", "--include-retired"]).output
    clash = _source(["create", "demo2", "--provider", "github", "--locator", "acme/demo", "--secret", "gh-demo"])
    assert clash.exit_code != 0
    assert "demo" in clash.output
    assert "enabled" in _source(["enable", "demo"]).output


def test_config_changes_carry_the_cli_door_and_no_secret_value(client: TestClient) -> None:
    _seed(client)
    client.patch("/api/work-sources/demo", json={"annotate": True})

    text = _cli("config", ["changes"])
    assert text.exit_code == 0, text.output
    lines = text.output.strip().splitlines()
    assert "api  work_source demo  r2  edit  annotate" in lines[0]
    assert "cli  work_source demo  r1  create" in lines[1]
    assert "cli  secret gh-demo  r1  create" in lines[2]
    assert _SENTINEL not in text.output

    raw = json.loads(_cli("config", ["changes", "--json", "--kind", "secret", "--all"]).output)
    assert [(c["door"], c["record_key"]) for c in raw["changes"]] == [("cli", "gh-demo")]
    assert _SENTINEL not in json.dumps(raw)
    assert "no configuration changes" in _cli("config", ["changes", "--key", "absent"]).output
