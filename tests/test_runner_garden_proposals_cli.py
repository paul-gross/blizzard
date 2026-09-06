"""``blizzard runner garden proposals`` (unit tier), mirroring
``tests/test_runner_garden_findings_cli.py``'s shape: ``httpx`` stubbed, no live socket. The
route itself (authorization, hub forward, 403/404/503) is the component tier's
``tests/test_runner_garden_proposals_api.py``.
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

_PROPOSALS_TEXT = (
    '[{"proposal_id": "gprop_1", "routine_name": "nightly", "class": "fix-the-source", "title": "t", "body": "b", '
    '"findings": ["fin_1"], "created_at": "2026-09-02T12:00:00Z", "closure": null}]'
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


def test_proposals_gets_the_lease_scoped_route_with_inherited_identity_and_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_get(url: str, *, headers: dict, timeout: float, **_: object) -> _FakeResponse:
        calls.append((url, headers))
        return _FakeResponse(text=_PROPOSALS_TEXT)

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(runner_group, ["garden", "proposals"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls == [
        ("http://127.0.0.1:8431/api/leases/lease_9/garden/proposals", {"X-Blizzard-Lease-Token": "the-lease-token"})
    ]
    assert result.output.strip() == _PROPOSALS_TEXT


def test_proposals_omits_the_token_header_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_get(url: str, *, headers: dict, timeout: float, **_: object) -> _FakeResponse:
        calls.append(headers)
        return _FakeResponse(text="[]")

    monkeypatch.setattr(httpx, "get", fake_get)
    env = {k: v for k, v in _ENV.items() if k != "BLIZZARD_LEASE_TOKEN"}
    result = CliRunner().invoke(runner_group, ["garden", "proposals"], env=env)

    assert result.exit_code == 0, result.output
    assert calls == [{}]


def test_proposals_errors_without_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    attempted = False

    def fake_get(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal attempted
        attempted = True
        return _FakeResponse()

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(
        runner_group, ["garden", "proposals"], env={"BLIZZARD_LEASE_ID": "", "BLIZZARD_RUNNER_URL": ""}
    )

    assert result.exit_code != 0
    assert "no BLIZZARD_LEASE_ID/BLIZZARD_RUNNER_URL" in result.output
    assert attempted is False


def test_proposals_surfaces_a_403_as_a_nonzero_exit_with_the_hub_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _RejectingResponse({"detail": "presented token does not authorize lease"})
    )
    result = CliRunner().invoke(runner_group, ["garden", "proposals"], env=_ENV)

    assert result.exit_code != 0
    assert "presented token does not authorize lease" in result.output


def test_proposals_surfaces_a_404_as_a_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _RejectingResponse({"detail": "chunk ch_1 carries no run context"})
    )
    result = CliRunner().invoke(runner_group, ["garden", "proposals"], env=_ENV)

    assert result.exit_code != 0
    assert "chunk ch_1 carries no run context" in result.output


def test_garden_proposals_help_names_no_routine_or_scope_or_chunk_flag() -> None:
    """The verb takes no flag naming a routine, a scope, or a chunk — the hub derives
    the routine from the chunk's own run context, so there is nothing here for a worker
    to point at another routine's docket."""
    result = CliRunner().invoke(runner_group, ["garden", "proposals", "--help"])

    assert result.exit_code == 0, result.output
    assert "--routine" not in result.output
    assert "--scope" not in result.output
    assert "--chunk" not in result.output
