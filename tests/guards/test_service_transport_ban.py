"""Ruff's ``TID251`` keeps runner command modules off ``UrllibServiceTransport``: they reach the OpenCode 2 service
through ``RunnerCliCollaborators.service`` (``bzh:dependency-injection``). The guard runs the pinned ruff over source
placed at real repo paths, so it judges the real config, scope and exemptions."""

from __future__ import annotations

import subprocess

import pytest

from tests.support.repo_files import repo_root

_OPENCODE_COMMAND = "src/blizzard/runner/cli/opencode.py"
_NEW_COMMAND = "src/blizzard/runner/cli/a_new_command.py"
_CARRIER = "src/blizzard/runner/cli/children.py"

_USES = {
    "direct import": (
        "from blizzard.runner.harness.opencode_v2.transport import UrllibServiceTransport\nUrllibServiceTransport()\n"
    ),
    "aliased import": ("from blizzard.runner.harness.opencode_v2.transport import UrllibServiceTransport as T\nT()\n"),
    "module-qualified": (
        "from blizzard.runner.harness.opencode_v2 import transport\ntransport.UrllibServiceTransport()\n"
    ),
    "module alias": ("import blizzard.runner.harness.opencode_v2.transport as m\nm.UrllibServiceTransport()\n"),
    "fully qualified": (
        "import blizzard.runner.harness.opencode_v2.transport\n"
        "blizzard.runner.harness.opencode_v2.transport.UrllibServiceTransport()\n"
    ),
}

_PERMITTED = {
    "an unrelated import": (
        _NEW_COMMAND,
        "from blizzard.runner.harness.opencode_v2.transport import IServiceTransport\n"
        "from blizzard.runner.harness.opencode.compatibility.loopback import ILoopbackTransport, UrllibLoopbackTransport\n"
        "from blizzard.runner.harness.opencode_v2.client import ModelReference\n"
        "print(IServiceTransport, ILoopbackTransport, UrllibLoopbackTransport, ModelReference)\n",
    ),
    "daemon declaration": (
        "src/blizzard/runner/harness/opencode_v2/declaration.py",
        "from blizzard.runner.harness.opencode_v2.transport import UrllibServiceTransport\nUrllibServiceTransport()\n",
    ),
    "daemon adapter": (
        "src/blizzard/runner/harness/opencode_v2/adapter.py",
        "from blizzard.runner.harness.opencode_v2.transport import UrllibServiceTransport\nUrllibServiceTransport()\n",
    ),
    "service tests": (
        "tests/service/harnesses/test_a_service.py",
        "from blizzard.runner.harness.opencode_v2.transport import UrllibServiceTransport\nUrllibServiceTransport()\n",
    ),
}


def _lint(path: str, source: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "--frozen", "ruff", "check", "--no-cache", "--stdin-filename", path, "-"],
        input=source,
        capture_output=True,
        text=True,
        cwd=repo_root(),
        check=False,
    )


@pytest.mark.parametrize("path", [_OPENCODE_COMMAND, _NEW_COMMAND])
@pytest.mark.parametrize("use", sorted(_USES))
def test_a_command_module_cannot_reach_the_urllib_service_transport(path: str, use: str) -> None:
    result = _lint(path, _USES[use])
    assert "TID251" in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize("use", sorted(_USES))
def test_the_carrier_may_build_the_urllib_service_transport(use: str) -> None:
    result = _lint(_CARRIER, _USES[use])
    assert "TID251" not in result.stdout, result.stdout


@pytest.mark.parametrize("case", sorted(_PERMITTED))
def test_code_outside_the_command_modules_is_not_banned(case: str) -> None:
    path, source = _PERMITTED[case]
    result = _lint(path, source)
    assert "TID251" not in result.stdout, result.stdout


def test_the_real_tree_is_clean() -> None:
    result = subprocess.run(
        ["uv", "run", "--frozen", "ruff", "check", "--no-cache", "."],
        capture_output=True,
        text=True,
        cwd=repo_root(),
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
