"""The secret routes and ``blizzard hub secret`` verbs (component tier) — a real
``build_hosted_app``, one planted value driven through every surface."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
import structlog.testing
from click.testing import CliRunner
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from blizzard import __version__
from blizzard.foundation.platform_tracing.handle import build_platform_tracing
from blizzard.hub import app as hub_app
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.cli import hub as hub_group
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.tracing.attributes import (
    PLATFORM_INSTRUMENTATION_SCOPE,
    PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
    resource_attributes,
)

pytestmark = pytest.mark.component

_SENTINEL = "tok-planted-839"
_HUB = "http://hub.local:8421"


@pytest.fixture
def config(tmp_path: Path) -> HubConfig:
    return hub_runtime.init_environment(tmp_path / "hub")


@pytest.fixture
def client(config: HubConfig) -> Iterator[TestClient]:
    with TestClient(hub_app.build_hosted_app(config)) as c:
        yield c


def _route_cli_through(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    """Send the CLI's ``httpx`` verbs to the in-process app."""

    def route(method: str):  # type: ignore[no-untyped-def]
        def call(url: str, *, timeout: float, **kwargs):  # type: ignore[no-untyped-def]
            kwargs.pop("headers", None)
            return client.request(method.upper(), urlsplit(url).path, **kwargs)

        return call

    for method in ("get", "post", "put"):
        monkeypatch.setattr(httpx, method, route(method))


def _cli(args: list[str], *, stdin: str | None = None):  # type: ignore[no-untyped-def]
    return CliRunner().invoke(hub_group, ["secret", *args], input=stdin, env={"BZ_HUB_URL": _HUB})


def test_create_replace_list_show_retire_enable_over_the_api(client: TestClient) -> None:
    created = client.post("/api/secrets", json={"name": "gh-test", "value": _SENTINEL})
    assert created.status_code == 201
    assert created.json()["revision"] == 1
    assert client.post("/api/secrets", json={"name": "gh-test", "value": "x"}).status_code == 409
    replaced = client.put("/api/secrets/gh-test/value", json={"value": "v2"}, headers={"If-Match": "1"})
    assert replaced.status_code == 200
    assert replaced.json()["revision"] == 2
    assert replaced.json()["replaced_by"] == "operator"
    stale = client.put("/api/secrets/gh-test/value", json={"value": "v3"}, headers={"If-Match": "1"})
    assert stale.status_code == 409
    assert "revision 2" in stale.json()["detail"]
    assert client.put("/api/secrets/nope/value", json={"value": "v"}).status_code == 404
    assert client.post("/api/secrets", json={"name": "Bad Name", "value": "v"}).status_code == 422

    assert client.post("/api/secrets/gh-test/retire").json()["retired"] is True
    assert client.get("/api/secrets").json() == []
    assert [s["name"] for s in client.get("/api/secrets?include_retired=true").json()] == ["gh-test"]
    assert client.put("/api/secrets/gh-test/value", json={"value": "v4"}).status_code == 409
    assert client.post("/api/secrets/gh-test/enable").json()["retired"] is False
    shown = client.get("/api/secrets/gh-test").json()
    assert shown["revision"] == 2
    assert set(shown) == {"name", "revision", "replaced_at", "replaced_by", "created_at", "retired"}
    assert client.get("/api/secrets/missing").status_code == 404


def test_a_validation_error_on_a_secret_route_echoes_no_input_but_other_routes_keep_theirs(client: TestClient) -> None:
    resp = client.post("/api/secrets", json={"value": _SENTINEL})
    assert resp.status_code == 422
    assert _SENTINEL not in resp.text
    assert "input" not in resp.json()["detail"][0]
    other = client.post("/api/scopes", json={"slug": 7})
    assert other.status_code == 422
    assert "input" in other.json()["detail"][0]


def test_secret_set_reads_stdin_only_and_refuses_empty(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _route_cli_through(monkeypatch, client)
    assert _cli(["set", "gh-test"], stdin=f"{_SENTINEL}\r\n").exit_code == 0
    again = _cli(["set", "gh-test"], stdin="v2\n")
    assert again.exit_code == 0
    assert "revision 2" in again.output
    empty = _cli(["set", "gh-test"], stdin="\n")
    assert empty.exit_code != 0
    assert "empty" in empty.output
    assert _cli(["set", "gh-test", "an-argument"], stdin="v\n").exit_code != 0
    assert _cli(["retire", "gh-test"]).exit_code == 0
    assert "gh-test" not in _cli(["list"]).output
    assert "gh-test  r2  retired" in _cli(["list", "--include-retired"]).output
    refused = _cli(["set", "gh-test"], stdin="v3\n")
    assert refused.exit_code != 0
    assert _cli(["enable", "gh-test"]).exit_code == 0
    assert "revision 2" in _cli(["show", "gh-test"]).output


def test_a_planted_value_appears_on_no_surface(
    config: HubConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = InMemorySpanExporter()
    handle = build_platform_tracing(
        replace(config.tracing, platform=True, platform_sample_ratio=1.0),
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"},
        resource=resource_attributes(os.environ, __version__),
        scope=PLATFORM_INSTRUMENTATION_SCOPE,
        scope_version=PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
        exporter=exporter,
    )
    seen: list[str] = []
    with (
        structlog.testing.capture_logs() as logs,
        TestClient(hub_app.build_hosted_app(config, platform_tracing=handle)) as client,
    ):
        _route_cli_through(monkeypatch, client)
        for resp in (
            client.post("/api/secrets", json={"name": "gh-test", "value": _SENTINEL}),
            client.post("/api/secrets", json={"value": _SENTINEL}),
            client.post("/api/secrets", json={"name": "gh-test", "value": _SENTINEL}),
            client.put("/api/secrets/gh-test/value", json={"value": _SENTINEL}, headers={"If-Match": "9"}),
            client.put("/api/secrets/gh-test/value", json={"value": _SENTINEL}),
            client.put("/api/secrets/gh-test/value", json={}),
            client.get("/api/secrets/gh-test"),
            client.get("/api/secrets"),
            client.post("/api/secrets/gh-test/retire"),
            client.post("/api/secrets/gh-test/enable"),
        ):
            seen.append(resp.text)
        for args in (
            ["list"],
            ["show", "gh-test"],
            ["list", "--json"],
            ["retire", "gh-test"],
            ["enable", "gh-test"],
        ):
            seen.append(_cli(args).output)
        seen.append(_cli(["set", "gh-test"], stdin=f"{_SENTINEL}\n").output)
    handle.shutdown(5.0)
    spans = repr([(s.name, dict(s.attributes or {})) for s in exporter.get_finished_spans()])
    surfaces = "\n".join([*seen, spans, repr(logs)])
    assert "gh-test" in surfaces, "positive control: the scan can see the secret's name"
    assert _SENTINEL not in surfaces

    stored = b"".join(p.read_bytes() for p in sorted(config.data_dir.rglob("*")) if p.is_file())
    stored += (tmp_path / "hub" / "hub.db").read_bytes() if (tmp_path / "hub" / "hub.db").exists() else b""
    assert b"gh-test" in stored, "positive control: the scan reads the store"
    assert _SENTINEL.encode() not in stored
