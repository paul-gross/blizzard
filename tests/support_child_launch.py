"""The runner's real launch seam and tool profile, for tests that launch a real child through a runner adapter.

Each helper reads the daemon env when called, so a test's ``monkeypatch.setenv`` lands before the profile is built."""

from __future__ import annotations

from blizzard.runner.environments.factory import WorkspaceLaunch
from blizzard.runner.environments.internal.basic_provider import CLONE_TIMEOUT
from blizzard.runner.environments.internal.git import SubprocessEnvGit
from blizzard.runner.harness.internal.version_probe import VersionProbe
from blizzard.runner.process.child_env import AllowlistedEnv, ChildEnv
from blizzard.runner.process.internal.subprocess_child_processes import SubprocessChildProcesses

#: The seam's one driver; stateless, so every test shares it.
CHILDREN = SubprocessChildProcesses()


def tool_env() -> ChildEnv:
    """The tool profile with no operator passthrough."""
    return AllowlistedEnv.of(()).tool()


def workspace_launch() -> WorkspaceLaunch:
    """A workspace binding's launch over the real driver and :func:`tool_env`."""
    return WorkspaceLaunch(children=CHILDREN, env=tool_env())


def version_probe() -> VersionProbe:
    """The harness version probe over the real driver and :func:`tool_env`."""
    return VersionProbe(CHILDREN, tool_env())


def env_git(timeout: float = CLONE_TIMEOUT) -> SubprocessEnvGit:
    """The workspace bindings' git over the real driver and :func:`tool_env`, at the basic binding's bound."""
    return SubprocessEnvGit(children=CHILDREN, env=tool_env(), timeout=timeout)
