"""``blizzard runner scope list`` (unit tier, blizzard#582 D2), mirroring
``tests/test_runner_garden_findings_cli.py``'s shape: ``httpx`` stubbed, no live socket.
The route itself is the component tier's ``tests/test_runner_scope_api.py``."""

from __future__ import annotations

import re

import httpx
import pytest
from click.testing import CliRunner

from blizzard.runner.cli import runner as runner_group

_ENV = {
    "BLIZZARD_LEASE_ID": "lease_9",
    "BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/",
    "BLIZZARD_LEASE_TOKEN": "the-lease-token",
}

_SCOPES_TEXT = '[{"slug": "blizzard", "description": "", "created_at": "2026-01-01T00:00:00Z", "retired": false}]'


class _FakeResponse:
    def __init__(self, text: str = "") -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return {}


class _RejectingResponse:
    def __init__(self, detail: dict | None = None) -> None:
        self._detail = detail or {}

    def raise_for_status(self) -> None:
        raise httpx.HTTPStatusError("403 forbidden", request=object(), response=self)  # type: ignore[arg-type]

    def json(self) -> object:
        return self._detail


@pytest.mark.unit
def test_list_gets_the_lease_scoped_route_with_inherited_identity_and_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_get(url: str, *, headers: dict, timeout: float, **_: object) -> _FakeResponse:
        calls.append((url, headers))
        return _FakeResponse(text=_SCOPES_TEXT)

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(runner_group, ["scope", "list"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls == [("http://127.0.0.1:8431/api/leases/lease_9/scopes", {"X-Blizzard-Lease-Token": "the-lease-token"})]
    assert result.output.strip() == _SCOPES_TEXT


@pytest.mark.unit
def test_list_errors_without_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    attempted = False

    def fake_get(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal attempted
        attempted = True
        return _FakeResponse()

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(
        runner_group, ["scope", "list"], env={"BLIZZARD_LEASE_ID": "", "BLIZZARD_RUNNER_URL": ""}
    )

    assert result.exit_code != 0
    assert "no BLIZZARD_LEASE_ID/BLIZZARD_RUNNER_URL" in result.output
    assert attempted is False


@pytest.mark.unit
def test_list_surfaces_a_403_as_a_nonzero_exit_with_the_hub_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _RejectingResponse({"detail": "presented token does not authorize lease"})
    )
    result = CliRunner().invoke(runner_group, ["scope", "list"], env=_ENV)

    assert result.exit_code != 0
    assert "presented token does not authorize lease" in result.output


@pytest.mark.unit
def test_scope_group_is_listed_in_top_level_help() -> None:
    result = CliRunner().invoke(runner_group, ["--help"])

    assert result.exit_code == 0, result.output
    assert re.search(r"^  scope\s", result.output, re.MULTILINE)


@pytest.mark.unit
def test_scope_group_names_list() -> None:
    result = CliRunner().invoke(runner_group, ["scope", "--help"])

    assert result.exit_code == 0, result.output
    assert re.search(r"^  list\s", result.output, re.MULTILINE)
