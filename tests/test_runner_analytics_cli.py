"""``blizzard runner analytics ...`` (unit tier, blizzard#545), mirroring
``tests/test_runner_garden_findings_cli.py``'s shape: ``httpx`` stubbed, no live socket.
The route itself (authorization, hub forward, 403/404/503) is the component tier's
``tests/test_runner_analytics_api.py``.
"""

from __future__ import annotations

import contextlib
import os
import re
import time
from collections.abc import Iterator

import httpx
import pytest
from click.testing import CliRunner

from blizzard.runner.cli import runner as runner_group


@contextlib.contextmanager
def _local_timezone(tz: str) -> Iterator[None]:
    """Pins the wall-clock zone ``--since``/``--until`` are read against — the same
    fixture ``tests/test_cli_analytics_events.py`` uses for the operator CLI's own D6
    conversion, since the conversion is genuinely machine-local otherwise."""
    original = os.environ.get("TZ")
    os.environ["TZ"] = tz
    time.tzset()
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


_ENV = {
    "BLIZZARD_LEASE_ID": "lease_9",
    "BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/",
    "BLIZZARD_LEASE_TOKEN": "the-lease-token",
}

_COUNTS_TEXT = '{"counts": [{"key": "a.py", "count": 3}]}'

#: Each verb's own argv suffix and the lease-scoped path it hits.
_VERBS = [
    (["analytics", "counts", "files"], "analytics/counts/files"),
    (["analytics", "counts", "skills"], "analytics/counts/skills"),
    (["analytics", "counts", "agent-types"], "analytics/counts/agent-types"),
    (["analytics", "counts", "nodes"], "analytics/counts/nodes"),
    (["analytics", "spend", "nodes"], "analytics/spend/nodes"),
    (["analytics", "spend", "graphs"], "analytics/spend/graphs"),
]


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


@pytest.mark.unit
@pytest.mark.parametrize(("argv", "path"), _VERBS)
def test_each_verb_hits_its_lease_scoped_path_with_since_converted_to_utc(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], path: str
) -> None:
    calls: list[tuple[str, dict, dict]] = []

    def fake_get(url: str, *, headers: dict, timeout: float, params: dict, **_: object) -> _FakeResponse:
        calls.append((url, headers, params))
        return _FakeResponse(text=_COUNTS_TEXT)

    monkeypatch.setattr(httpx, "get", fake_get)
    with _local_timezone("America/New_York"):  # UTC-5 in January, no DST
        result = CliRunner().invoke(runner_group, [*argv, "--since", "2026-01-01T10:00:00"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    url, headers, params = calls[0]
    assert url == f"http://127.0.0.1:8431/api/leases/lease_9/{path}"
    assert headers == {"X-Blizzard-Lease-Token": "the-lease-token"}
    assert params == {"since": "2026-01-01T15:00:00+00:00"}
    assert result.output.strip() == _COUNTS_TEXT


@pytest.mark.unit
@pytest.mark.parametrize(("argv", "path"), _VERBS)
def test_until_is_carried_through_when_given(monkeypatch: pytest.MonkeyPatch, argv: list[str], path: str) -> None:
    calls: list[dict] = []

    def fake_get(url: str, *, headers: dict, timeout: float, params: dict, **_: object) -> _FakeResponse:
        calls.append(params)
        return _FakeResponse(text=_COUNTS_TEXT)

    monkeypatch.setattr(httpx, "get", fake_get)
    with _local_timezone("America/New_York"):  # UTC-5 in January, no DST
        result = CliRunner().invoke(
            runner_group, [*argv, "--since", "2026-01-01T10:00:00", "--until", "2026-01-01T12:00:00"], env=_ENV
        )

    assert result.exit_code == 0, result.output
    assert calls == [{"since": "2026-01-01T15:00:00+00:00", "until": "2026-01-01T17:00:00+00:00"}]


@pytest.mark.unit
@pytest.mark.parametrize(("argv", "_path"), _VERBS)
def test_since_is_required(monkeypatch: pytest.MonkeyPatch, argv: list[str], _path: str) -> None:
    attempted = False

    def fake_get(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal attempted
        attempted = True
        return _FakeResponse()

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(runner_group, argv, env=_ENV)

    assert result.exit_code != 0
    assert "--since" in result.output
    assert attempted is False


@pytest.mark.unit
@pytest.mark.parametrize(("argv", "_path"), _VERBS)
def test_errors_without_identity(monkeypatch: pytest.MonkeyPatch, argv: list[str], _path: str) -> None:
    attempted = False

    def fake_get(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal attempted
        attempted = True
        return _FakeResponse()

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(
        runner_group,
        [*argv, "--since", "2026-01-01T00:00:00"],
        env={"BLIZZARD_LEASE_ID": "", "BLIZZARD_RUNNER_URL": ""},
    )

    assert result.exit_code != 0
    assert "no BLIZZARD_LEASE_ID/BLIZZARD_RUNNER_URL" in result.output
    assert attempted is False


@pytest.mark.unit
@pytest.mark.parametrize(("argv", "_path"), _VERBS)
def test_surfaces_a_404_as_a_nonzero_exit(monkeypatch: pytest.MonkeyPatch, argv: list[str], _path: str) -> None:
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _RejectingResponse({"detail": "chunk ch_1 carries no run context"})
    )
    result = CliRunner().invoke(runner_group, [*argv, "--since", "2026-01-01T00:00:00"], env=_ENV)

    assert result.exit_code != 0
    assert "chunk ch_1 carries no run context" in result.output


@pytest.mark.unit
def test_analytics_group_is_listed_in_top_level_help() -> None:
    result = CliRunner().invoke(runner_group, ["--help"])

    assert result.exit_code == 0, result.output
    assert re.search(r"^  analytics\s", result.output, re.MULTILINE)


@pytest.mark.unit
def test_analytics_help_names_no_routine_or_scope_flag() -> None:
    """The routine and scope are both derived server-side from the worker's own lease —
    no verb here takes a flag naming either."""
    for argv in (["analytics", "counts", "files", "--help"], ["analytics", "spend", "nodes", "--help"]):
        result = CliRunner().invoke(runner_group, argv)
        assert result.exit_code == 0, result.output
        assert "--routine" not in result.output
        assert "--scope" not in result.output
