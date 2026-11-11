"""Runner control verbs reject unreadable successful responses at the command boundary."""

from dataclasses import replace
from pathlib import Path

import httpx2
import pytest
from click.testing import CliRunner

from blizzard.runner.cli import runner as runner_group
from blizzard.runner.cli.children import RunnerCliCollaborators
from blizzard.runner.process.child_env import ChildEnv
from blizzard.runner.process.child_processes import ShellCommand
from blizzard.runner.process.internal.subprocess_child_processes import SubprocessChildProcesses
from tests.support.worker_http import bind_transport


class _Children(SubprocessChildProcesses):
    def __init__(self) -> None:
        self.calls = 0

    def run_foreground(self, command: ShellCommand, *, cwd: Path | str, env: ChildEnv) -> int:
        self.calls += 1
        return 0


@pytest.mark.parametrize(
    ("args", "target"),
    [
        (["pause"], "/api/runner"),
        (["start"], "/api/runner"),
        (["takeover", "ch_1"], "/api/chunks/ch_1/takeovers"),
        (["selftest", "claude_code"], "/api/selftests"),
    ],
)
def test_control_rejects_malformed_success_without_output_or_launch(args: list[str], target: str) -> None:
    requests: list[str] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.url.path)
        if request.url.path == "/api/host/runners":
            return httpx2.Response(
                200, json={"items": [{"name": "r1", "runner_id": "rn_1", "state": "serving", "served": True}]}
            )
        assert request.url.path == f"/runners/rn_1{target}"
        return httpx2.Response(200, json={"secret": "do-not-print"})

    bound = bind_transport(answer)
    children = _Children()
    carrier = replace(RunnerCliCollaborators.extending(bound.collaborators), children=children)
    result = CliRunner().invoke(runner_group, [*args, "--runner-url", "http://user:secret@runner.test"], obj=carrier)

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "unreadable answer" in result.stderr
    assert f"{args[0]}: unreadable answer from the runner at http://runner.test" in result.stderr
    assert "secret" not in result.stderr
    assert "do-not-print" not in result.stderr
    assert children.calls == 0
    assert requests == ["/api/host/runners", f"/runners/rn_1{target}"]


def test_selftest_rejects_malformed_poll_without_printing_partial_checks() -> None:
    from blizzard.foundation.clock import ManualMonotonicClock

    paths: list[str] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/host/runners":
            return httpx2.Response(
                200, json={"items": [{"name": "r1", "runner_id": "rn_1", "state": "serving", "served": True}]}
            )
        if request.url.path == "/runners/rn_1/api/selftests":
            return httpx2.Response(
                200, json={"id": "st_1", "harness": "claude_code", "status": "running", "settled": False, "checks": []}
            )
        assert request.url.path == "/runners/rn_1/api/selftests/st_1"
        return httpx2.Response(200, json={"secret": "do-not-print"})

    bound = bind_transport(answer)
    carrier = replace(bound.collaborators, monotonic_clock=ManualMonotonicClock())
    result = CliRunner().invoke(
        runner_group, ["selftest", "claude_code", "--runner-url", "http://runner.test"], obj=carrier
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "unreadable answer" in result.stderr
    assert "selftest: unreadable answer from the runner at http://runner.test" in result.stderr
    assert "do-not-print" not in result.stderr
    assert paths == ["/api/host/runners", "/runners/rn_1/api/selftests", "/runners/rn_1/api/selftests/st_1"]
