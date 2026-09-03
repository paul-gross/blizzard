"""``blizzard runner garden findings`` (unit tier, D4), mirroring
``tests/test_runner_chunk_history_cli.py``'s shape: ``httpx`` stubbed, no live socket. The
route itself (authorization, hub forward, 403/404/503) is the component tier's
``tests/test_runner_garden_findings_api.py``.
"""

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

_FINDINGS_TEXT = (
    '[{"finding_id": "fin_1", "routine_name": "nightly", "scope_slug": "blizzard", "class": "stale-docstring", '
    '"locus": "a.py:1", "summary": "s", "introduced": null, "live": true, "state": "live", "note": null, '
    '"last_seen_at": null, "observed_count": 0}]'
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
        raise httpx.HTTPStatusError("403 forbidden", request=object(), response=self)  # type: ignore[arg-type]

    def json(self) -> object:
        return self._detail


def test_findings_gets_the_lease_scoped_route_with_inherited_identity_and_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_get(url: str, *, headers: dict, timeout: float, **_: object) -> _FakeResponse:
        calls.append((url, headers))
        return _FakeResponse(text=_FINDINGS_TEXT)

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(runner_group, ["garden", "findings"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls == [
        ("http://127.0.0.1:8431/api/leases/lease_9/garden/findings", {"X-Blizzard-Lease-Token": "the-lease-token"})
    ]
    assert result.output.strip() == _FINDINGS_TEXT


def test_findings_omits_the_token_header_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_get(url: str, *, headers: dict, timeout: float, **_: object) -> _FakeResponse:
        calls.append(headers)
        return _FakeResponse(text="[]")

    monkeypatch.setattr(httpx, "get", fake_get)
    env = {k: v for k, v in _ENV.items() if k != "BLIZZARD_LEASE_TOKEN"}
    result = CliRunner().invoke(runner_group, ["garden", "findings"], env=env)

    assert result.exit_code == 0, result.output
    assert calls == [{}]


def test_findings_errors_without_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    attempted = False

    def fake_get(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal attempted
        attempted = True
        return _FakeResponse()

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(
        runner_group, ["garden", "findings"], env={"BLIZZARD_LEASE_ID": "", "BLIZZARD_RUNNER_URL": ""}
    )

    assert result.exit_code != 0
    assert "no BLIZZARD_LEASE_ID/BLIZZARD_RUNNER_URL" in result.output
    assert attempted is False


def test_findings_surfaces_a_403_as_a_nonzero_exit_with_the_hub_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _RejectingResponse({"detail": "presented token does not authorize lease"})
    )
    result = CliRunner().invoke(runner_group, ["garden", "findings"], env=_ENV)

    assert result.exit_code != 0
    assert "presented token does not authorize lease" in result.output


def test_findings_surfaces_a_404_as_a_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _RejectingResponse({"detail": "chunk ch_1 carries no run context"})
    )
    result = CliRunner().invoke(runner_group, ["garden", "findings"], env=_ENV)

    assert result.exit_code != 0
    assert "chunk ch_1 carries no run context" in result.output


def test_garden_findings_help_names_no_routine_or_scope_flag() -> None:
    """D5: the verb takes no flag naming a routine or a scope — the hub derives both
    from the chunk's own run context, so there is nothing here for a worker to point at
    another routine's bucket."""
    result = CliRunner().invoke(runner_group, ["garden", "findings", "--help"])

    assert result.exit_code == 0, result.output
    assert "--routine" not in result.output
    assert "--scope" not in result.output
    assert "--chunk" not in result.output


def test_garden_group_is_listed_in_top_level_help() -> None:
    """A bare substring check would pass even with the group unregistered — matches
    click's actual `Commands:` listing shape instead."""
    result = CliRunner().invoke(runner_group, ["--help"])

    assert result.exit_code == 0, result.output
    assert re.search(r"^  garden\s", result.output, re.MULTILINE)


def test_garden_group_names_only_findings() -> None:
    """D4: one new verb, not two — no routine-read verb alongside it."""
    result = CliRunner().invoke(runner_group, ["garden", "--help"])

    assert result.exit_code == 0, result.output
    assert re.search(r"^  findings\s", result.output, re.MULTILINE)
