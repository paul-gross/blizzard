"""``blizzard runner opencode drive``: with no managed service registered it still writes a ``failed`` result naming
the service, and a request it cannot read fails the command without one."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.runner.cli import runner as runner_group
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.opencode_v2.errors import ErrorKind
from blizzard.runner.harness.opencode_v2.request import DriveKind, DriveRequest, write_request
from blizzard.runner.harness.opencode_v2.result import Outcome, parse_result


def test_no_registered_service_writes_a_failed_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    request = DriveRequest(
        kind=DriveKind.SPAWN,
        session_id="ses_1",
        directory=str(tmp_path),
        prompt="p",
        runner_id="run_1",
        lease_id="lease_1",
        autonomy=Autonomy.Normal,
    )
    path = write_request(tmp_path / "requests", request)
    answer = CliRunner().invoke(runner_group, ["opencode", "drive", "--request", str(path)])
    assert answer.exit_code == 0, answer.output
    result = parse_result(answer.output)
    assert result is not None and result.outcome is Outcome.FAILED
    assert result.error is not None and result.error.kind is ErrorKind.SERVICE
    assert not path.exists()


def test_an_unreadable_request_fails_the_command(tmp_path: Path) -> None:
    answer = CliRunner().invoke(runner_group, ["opencode", "drive", "--request", str(tmp_path / "absent.json")])
    assert answer.exit_code != 0
    assert parse_result(answer.output) is None
