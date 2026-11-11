from __future__ import annotations

from typing import Any

import httpx2
import pytest
from click.testing import CliRunner

from blizzard.runner.cli.traces import traces_group
from tests.support.worker_http import bind_transport

_STATUS: dict[str, Any] = {
    "state": "disabled",
    "endpoint": None,
    "rejected_setting": None,
    "rejected_value": None,
    "cursor_at": None,
    "lag_seconds": None,
    "last_export_at": None,
    "last_export_span_count": None,
    "last_error_at": None,
    "last_error_message": None,
    "last_error_ongoing": False,
    "replay_max_window_seconds": 86400,
    "extra": "kept",
}


class _Daemon:
    def get(self, path: str) -> httpx2.Response:
        return httpx2.Response(200, json=_STATUS)

    def post(self, path: str) -> httpx2.Response:
        return httpx2.Response(200, json={"leases": 2, "spans": 3, "batches": 1, "dry_run": True})

    def answer(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/host/runners":
            return httpx2.Response(
                200, json={"items": [{"runner_id": "rn_1", "name": "r", "state": "serving", "served": True}]}
            )
        if request.method == "GET":
            return self.get(request.url.path)
        return self.post(request.url.path)


def _invoke(daemon: _Daemon, args: list[str]) -> Any:
    bound = bind_transport(daemon.answer)
    return CliRunner().invoke(traces_group, [*args, "--runner-url", "http://runner"], obj=bound.collaborators)


def test_traces_json_stdout_baselines() -> None:
    status = _invoke(_Daemon(), ["status", "--json"])
    assert status.exit_code == 0, status.output
    import json

    assert status.stdout == json.dumps(_STATUS, indent=2) + "\n"
    replay = _invoke(
        _Daemon(),
        ["replay", "--since", "2020-01-01T00:00:00Z", "--until", "2020-01-01T01:00:00Z", "--dry-run", "--json"],
    )
    assert replay.exit_code == 0, replay.output
    assert replay.stdout == (
        '{\n  "leases": 2,\n  "spans": 3,\n  "batches": 1,\n  "windows": 1,\n  "dry_run": true\n}\n'
    )


@pytest.mark.parametrize("as_json", [False, True])
def test_status_rejects_invalid_response_before_output(as_json: bool) -> None:
    class Invalid(_Daemon):
        def get(self, path: str) -> httpx2.Response:
            return httpx2.Response(200, json={"state": "disabled", "secret": "do-not-print"})

    result = _invoke(Invalid(), ["status", *(["--json"] if as_json else [])])
    assert result.exit_code != 0
    assert result.stdout == ""
    assert "unreadable answer" in result.stderr
    assert "do-not-print" not in result.output


def test_replay_rejects_later_invalid_response_without_progress() -> None:
    class Invalid(_Daemon):
        calls = 0

        def post(self, path: str) -> httpx2.Response:
            self.calls += 1
            if self.calls == 2:
                return httpx2.Response(
                    200, json={"secret": "do-not-print"}, request=httpx2.Request("POST", "http://runner/")
                )
            return super().post(path)

    result = _invoke(Invalid(), ["replay", "--since", "2020-01-01T00:00:00Z", "--until", "2020-01-03T00:00:00Z"])
    assert result.exit_code != 0
    assert result.stdout == ""
    assert "unreadable answer" in result.stderr
    assert "window 2 of 2" in result.stderr
    assert "window 1 of 2" not in result.stderr
    assert "do-not-print" not in result.output


@pytest.mark.parametrize("width", [0, -1])
def test_replay_rejects_unusable_window_width(width: int) -> None:
    class Invalid(_Daemon):
        def get(self, path: str) -> httpx2.Response:
            return httpx2.Response(200, json={**_STATUS, "replay_max_window_seconds": width})

    result = _invoke(Invalid(), ["replay", "--since", "2020-01-01T00:00:00Z", "--until", "2020-01-02T00:00:00Z"])
    assert result.exit_code != 0
    assert result.stdout == ""
    assert "unreadable answer" in result.stderr


@pytest.mark.parametrize("status_code", [200, 502])
def test_replay_rejects_invalid_window_body(status_code: int) -> None:
    class Invalid(_Daemon):
        def post(self, path: str) -> httpx2.Response:
            return httpx2.Response(
                status_code, json={"detail": "secret"}, request=httpx2.Request("POST", "http://runner/")
            )

    result = _invoke(
        Invalid(), ["replay", "--since", "2020-01-01T00:00:00Z", "--until", "2020-01-01T01:00:00Z", "--json"]
    )
    assert result.exit_code != 0
    assert result.stdout == ""
    assert "unreadable answer" in result.stderr
    assert "secret" not in result.output
