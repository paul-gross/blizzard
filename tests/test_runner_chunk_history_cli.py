"""``blizzard runner chunk history`` (unit tier, issue #237), mirroring
``tests/test_runner_artifact_cli.py``'s shape: ``httpx`` stubbed, no live socket. The
route itself (store round-trip, hub forward, 403/404/503) is the component tier's
``tests/test_runner_chunk_history_api.py``.
"""

from __future__ import annotations

import httpx
import pytest
from click.testing import CliRunner

from blizzard.runner.cli import runner as runner_group

_ENV = {
    "BLIZZARD_LEASE_ID": "lease_9",
    "BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/",
    "BLIZZARD_LEASE_TOKEN": "the-lease-token",
}

_HISTORY_TEXT = (
    '[{"kind": "transition", "from_node": "build", "to_node": "review", "choice": "ready", '
    '"epoch": 1, "graph_name": "adv-dwf", "cause": null, "detail": null, '
    '"recorded_at": "2026-07-21T10:00:00+00:00"}]'
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


def test_history_gets_the_lease_scoped_route_with_inherited_identity_and_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_get(url: str, *, headers: dict, timeout: float) -> _FakeResponse:
        calls.append((url, headers))
        return _FakeResponse(text=_HISTORY_TEXT)

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(runner_group, ["chunk", "history"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls == [
        ("http://127.0.0.1:8431/api/leases/lease_9/history", {"X-Blizzard-Lease-Token": "the-lease-token"})
    ]
    assert result.output.strip() == _HISTORY_TEXT


def test_history_omits_the_token_header_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_get(url: str, *, headers: dict, timeout: float) -> _FakeResponse:
        calls.append(headers)
        return _FakeResponse(text="[]")

    monkeypatch.setattr(httpx, "get", fake_get)
    env = {k: v for k, v in _ENV.items() if k != "BLIZZARD_LEASE_TOKEN"}
    result = CliRunner().invoke(runner_group, ["chunk", "history"], env=env)

    assert result.exit_code == 0, result.output
    assert calls == [{}]


def test_history_errors_without_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    attempted = False

    def fake_get(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal attempted
        attempted = True
        return _FakeResponse()

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(
        runner_group, ["chunk", "history"], env={"BLIZZARD_LEASE_ID": "", "BLIZZARD_RUNNER_URL": ""}
    )

    assert result.exit_code != 0
    assert "no BLIZZARD_LEASE_ID/BLIZZARD_RUNNER_URL" in result.output
    assert attempted is False


def test_history_surfaces_a_403_as_a_nonzero_exit_with_the_hub_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _RejectingResponse({"detail": "presented token does not authorize lease"})
    )
    result = CliRunner().invoke(runner_group, ["chunk", "history"], env=_ENV)

    assert result.exit_code != 0
    assert "presented token does not authorize lease" in result.output


def test_history_surfaces_a_404_as_a_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _RejectingResponse({"detail": "no active lease lease_9"}))
    result = CliRunner().invoke(runner_group, ["chunk", "history"], env=_ENV)

    assert result.exit_code != 0
    assert "no active lease lease_9" in result.output


def test_chunk_history_help_names_no_chunk_naming_flag() -> None:
    result = CliRunner().invoke(runner_group, ["chunk", "history", "--help"])

    assert result.exit_code == 0, result.output
    assert "--chunk" not in result.output
    assert "--lease" not in result.output


def test_chunk_group_is_listed_in_top_level_help() -> None:
    result = CliRunner().invoke(runner_group, ["--help"])

    assert result.exit_code == 0, result.output
    assert "chunk" in result.output
