"""``blizzard runner finding list``/``get`` (unit tier, blizzard#397 Phase 2), mirroring
``tests/test_runner_garden_findings_cli.py``'s shape: ``httpx`` stubbed, no live socket.
The route itself (authorization, hub forward, 403/404/503) is the component tier's
``tests/test_runner_finding_api.py``."""

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

_LIST_TEXT = (
    '[{"finding_id": "fin_1", "routine_name": "nightly", "scope_slug": "blizzard", "class": "stale-docstring", '
    '"locus": "a.py:1", "summary": "s", "introduced": null, "live": true, "state": "live", "note": null, '
    '"last_seen_at": null, "observed_count": 0}]'
)
_GET_TEXT = (
    '{"finding_id": "fin_1", "routine_name": "nightly", "scope_slug": "blizzard", "class": "stale-docstring", '
    '"locus": "a.py:1", "summary": "s", "introduced": null, "live": true, "state": "live", "note": null, '
    '"last_seen_at": null, "observed_count": 0}'
)


class _FakeResponse:
    def __init__(self, text: str = "", payload: object | None = None) -> None:
        self.text = text
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _RejectingResponse:
    def __init__(self, detail: dict | None = None) -> None:
        self._detail = detail or {}

    def raise_for_status(self) -> None:
        raise httpx.HTTPStatusError("404 not found", request=object(), response=self)  # type: ignore[arg-type]

    def json(self) -> object:
        return self._detail


def test_list_gets_the_lease_scoped_route_with_inherited_identity_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_get(url: str, *, headers: dict, timeout: float, **_: object) -> _FakeResponse:
        calls.append((url, headers))
        return _FakeResponse(text=_LIST_TEXT)

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(runner_group, ["finding", "list"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls == [
        ("http://127.0.0.1:8431/api/leases/lease_9/findings", {"X-Blizzard-Lease-Token": "the-lease-token"})
    ]
    assert result.output.strip() == _LIST_TEXT


def test_list_omits_the_token_header_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_get(url: str, *, headers: dict, timeout: float, **_: object) -> _FakeResponse:
        calls.append(headers)
        return _FakeResponse(text="[]")

    monkeypatch.setattr(httpx, "get", fake_get)
    env = {k: v for k, v in _ENV.items() if k != "BLIZZARD_LEASE_TOKEN"}
    result = CliRunner().invoke(runner_group, ["finding", "list"], env=env)

    assert result.exit_code == 0, result.output
    assert calls == [{}]


def test_list_errors_without_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    attempted = False

    def fake_get(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal attempted
        attempted = True
        return _FakeResponse()

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(
        runner_group, ["finding", "list"], env={"BLIZZARD_LEASE_ID": "", "BLIZZARD_RUNNER_URL": ""}
    )

    assert result.exit_code != 0
    assert "no BLIZZARD_LEASE_ID/BLIZZARD_RUNNER_URL" in result.output
    assert attempted is False


def test_list_surfaces_a_404_as_a_nonzero_exit_with_the_hub_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _RejectingResponse({"detail": "chunk ch_1 answers no accepted proposal"})
    )
    result = CliRunner().invoke(runner_group, ["finding", "list"], env=_ENV)

    assert result.exit_code != 0
    assert "chunk ch_1 answers no accepted proposal" in result.output


def test_get_gets_the_lease_scoped_route_naming_the_finding_id(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_get(url: str, *, headers: dict, timeout: float, **_: object) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(text=_GET_TEXT)

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(runner_group, ["finding", "get", "fin_1"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls == ["http://127.0.0.1:8431/api/leases/lease_9/findings/fin_1"]
    assert result.output.strip() == _GET_TEXT


def test_get_surfaces_a_404_as_a_nonzero_exit_with_the_hub_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _RejectingResponse({"detail": "finding fin_other is not among the findings"})
    )
    result = CliRunner().invoke(runner_group, ["finding", "get", "fin_other"], env=_ENV)

    assert result.exit_code != 0
    assert "not among the findings" in result.output


def test_finding_verbs_help_names_no_chunk_routine_or_scope_flag() -> None:
    list_help = CliRunner().invoke(runner_group, ["finding", "list", "--help"])
    get_help = CliRunner().invoke(runner_group, ["finding", "get", "--help"])

    assert list_help.exit_code == 0, list_help.output
    assert get_help.exit_code == 0, get_help.output
    for output in (list_help.output, get_help.output):
        assert "--routine" not in output
        assert "--scope" not in output
        assert "--chunk" not in output


def test_finding_group_is_listed_in_top_level_help() -> None:
    result = CliRunner().invoke(runner_group, ["--help"])

    assert result.exit_code == 0, result.output
    assert re.search(r"^  finding\s", result.output, re.MULTILINE)


def test_finding_group_names_only_list_and_get() -> None:
    result = CliRunner().invoke(runner_group, ["finding", "--help"])

    assert result.exit_code == 0, result.output
    assert re.search(r"^  get\s", result.output, re.MULTILINE)
    assert re.search(r"^  list\s", result.output, re.MULTILINE)
