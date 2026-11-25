"""The hub-level settings CLI speaks the admin API, never a tenant's settings."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import httpx2
import pytest
from click.testing import CliRunner

from blizzard.hub.cli import hub
from tests.support.worker_http import bind_stubs


@pytest.mark.parametrize(
    "verb,args",
    [("get", []), ("set", ["a", "1"]), ("unset", ["a"]), ("changes", []), ("apply", ["file"]), ("export", [])],
)
def test_no_tenant(verb: str, args: list[str]) -> None:
    result = CliRunner().invoke(hub, ["admin", "settings", verb, *args, "--tenant", "t"])
    assert result.exit_code == 2
    assert "No such option '--tenant'" in result.output


def test_help_hides_aliases() -> None:
    result = CliRunner().invoke(hub, ["admin", "settings", "--help"])
    assert result.exit_code == 0
    assert "get" in result.output
    assert "\n  show " not in result.output
    assert "\n  read " not in result.output


def test_commands(tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []
    view = {"revision": 7, "settings": [{"key": "sweep.interval", "value": 2, "source": "entry", "lifetime": "pass"}]}

    def request(method: str, url: str, **kwargs: Any) -> httpx2.Response:
        calls.append((method, url, kwargs))
        if url.endswith("/changes"):
            body: Any = {"changes": [], "next_before": None}
        elif url.endswith("/apply"):
            body = {"dry_run": True, "apply_id": None, "outcomes": []}
        elif url.endswith("/export"):
            body = {"sweep": {"interval": 2}}
        else:
            body = view
        return httpx2.Response(200, json=body)

    runner = bind_stubs(request=request).runner

    def invoke(*args: str) -> Any:
        return runner.invoke(hub, ["admin", "settings", *args, "--hub-url", "http://testserver"])

    for verb in ("get", "show", "read"):
        result = invoke(verb, "sweep.interval", "--json")
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["key"] == "sweep.interval"
    result = invoke("set", "sweep.interval", "3")
    assert result.exit_code == 0, result.output
    assert calls[-1][2]["json"] == {"set": {"sweep.interval": 3}, "unset": []}
    assert calls[-1][2]["headers"]["If-Match"] == "7"
    assert invoke("unset", "sweep.interval").exit_code == 0
    assert calls[-1][2]["json"]["unset"] == ["sweep.interval"]
    assert invoke("changes").exit_code == 0
    file = tmp_path / "settings.toml"
    file.write_text("[sweep]\ninterval = 2\n")
    assert invoke("apply", str(file), "--dry-run").exit_code == 0
    assert calls[-1][2]["json"] == {"sweep": {"interval": 2}}
    assert calls[-1][2]["params"] == {"dry_run": "true"}
    result = invoke("export")
    assert result.exit_code == 0, result.output
    assert tomllib.loads(result.output) == {"sweep": {"interval": 2}}


def test_export_applies_back_and_invalid_toml_is_refused(tmp_path: Path) -> None:
    document = {"sweep": {"interval": 30, "enabled": False, "rules": [{"name": "☃️ 🌨", "limits": [1, 2]}]}}
    calls: list[tuple[str, dict[str, Any]]] = []

    def request(method: str, url: str, **kwargs: Any) -> httpx2.Response:
        calls.append((method, kwargs))
        body: Any = document if method == "get" else {"dry_run": False, "apply_id": None, "outcomes": []}
        return httpx2.Response(200, json=body)

    runner = bind_stubs(request=request).runner

    def invoke(*args: str) -> Any:
        return runner.invoke(hub, ["admin", "settings", *args, "--hub-url", "http://testserver"])

    exported = invoke("export")
    assert exported.exit_code == 0, exported.output
    file = tmp_path / "export.toml"
    file.write_text(exported.output)
    applied = invoke("apply", str(file))
    assert applied.exit_code == 0, applied.output
    assert calls[-1][1]["json"] == document

    before = len(calls)
    invalid = invoke("set", "sweep.interval", "[broken")
    assert invalid.exit_code != 0
    assert "VALUE is not valid TOML" in invalid.output
    file.write_text("[broken")
    invalid = invoke("apply", str(file))
    assert invalid.exit_code != 0
    assert str(file) in invalid.output
    assert len(calls) == before


@pytest.mark.parametrize("value", ["2026-01-01", "12:30:00", "2026-01-01T12:30:00Z", "[2026-01-01]"])
def test_toml_dates_are_cleanly_refused(tmp_path: Path, value: str) -> None:
    calls: list[str] = []

    def request(method: str, url: str, **kwargs: Any) -> httpx2.Response:
        calls.append(url)
        return httpx2.Response(200, json={})

    runner = bind_stubs(request=request).runner
    file = tmp_path / "settings.toml"
    file.write_text(f"[nested]\nvalue = {value}\n")
    for args in (("set", "--hub-url", "http://testserver", "--", "key", value), ("apply", str(file))):
        result = runner.invoke(hub, ["admin", "settings", *args])
        assert result.exit_code == 1
        assert "TOML date or time" in result.output or "unsupported TOML value" in result.output
    assert calls == []


@pytest.mark.parametrize(
    "name,content",
    [
        ("settings.toml", b"[sweep]\ninterval = 2\n"),
        ("settings.yaml", b"sweep:\n  interval: 2\n"),
        ("settings.json", b'{"sweep":{"interval":2}}'),
    ],
)
def test_apply_formats_send_json(tmp_path: Path, name: str, content: bytes) -> None:
    calls: list[dict[str, Any]] = []

    def request(method: str, url: str, **kwargs: Any) -> httpx2.Response:
        calls.append(kwargs)
        return httpx2.Response(200, json={"dry_run": False, "apply_id": None, "outcomes": []})

    file = tmp_path / name
    file.write_bytes(content)
    result = bind_stubs(request=request).runner.invoke(
        hub, ["admin", "settings", "apply", str(file), "--hub-url", "http://testserver"]
    )
    assert result.exit_code == 0, result.output
    assert calls[0]["json"] == {"sweep": {"interval": 2}}


@pytest.mark.parametrize(
    "name,content",
    [
        ("settings.txt", b"anything"),
        ("settings.yaml", b"a: ["),
        ("settings.json", b'{"a":'),
        ("settings.toml", b"[broken"),
        ("settings.yaml", b"- not a mapping"),
        ("settings.yaml", b"nested:\n  1: value"),
        ("settings.yaml", b"nested: &cycle [*cycle]"),
    ],
)
def test_invalid_document_never_reaches_transport(tmp_path: Path, name: str, content: bytes) -> None:
    calls: list[str] = []

    def request(method: str, url: str, **kwargs: Any) -> httpx2.Response:
        calls.append(url)
        return httpx2.Response(200, json={})

    file = tmp_path / name
    file.write_bytes(content)
    result = bind_stubs(request=request).runner.invoke(
        hub, ["admin", "settings", "apply", str(file), "--hub-url", "http://testserver"]
    )
    assert result.exit_code != 0
    assert calls == []


@pytest.mark.parametrize("content", [b"a: \xff\n", b"a: \x00\n", b"a: !!int invalid\n"])
def test_invalid_yaml_reader_and_constructor_failures_are_reported(tmp_path: Path, content: bytes) -> None:
    calls: list[str] = []

    def request(method: str, url: str, **kwargs: Any) -> httpx2.Response:
        calls.append(url)
        return httpx2.Response(200, json={})

    file = tmp_path / "settings.yaml"
    file.write_bytes(content)
    result = bind_stubs(request=request).runner.invoke(
        hub, ["admin", "settings", "apply", str(file), "--hub-url", "http://testserver"]
    )

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert str(file) in result.output
    assert calls == []


def test_export_json_keeps_raw_and_unencodable_toml_is_refused() -> None:
    def request(method: str, url: str, **kwargs: Any) -> httpx2.Response:
        return httpx2.Response(200, json={"nested": {"value": None}})

    runner = bind_stubs(request=request).runner
    json_result = runner.invoke(hub, ["admin", "settings", "export", "--json", "--hub-url", "http://testserver"])
    assert json_result.exit_code == 0, json_result.output
    assert json.loads(json_result.output) == {"nested": {"value": None}}
    result = runner.invoke(hub, ["admin", "settings", "export", "--hub-url", "http://testserver"])
    assert result.exit_code == 1
    assert "Error:" in result.output


@pytest.mark.parametrize("value", ["nan", "+inf", "-inf", "[nan]"])
def test_toml_nonfinite_numbers_are_cleanly_refused(tmp_path: Path, value: str) -> None:
    file = tmp_path / "settings.toml"
    file.write_text(f"[nested]\nvalue = {value}\n")
    runner = CliRunner()
    for args in (("set", "--hub-url", "http://testserver", "--", "key", value), ("apply", str(file))):
        result = runner.invoke(hub, ["admin", "settings", *args])
        assert result.exit_code == 1
        assert "non-finite number" in result.output or "nonfinite floats" in result.output
