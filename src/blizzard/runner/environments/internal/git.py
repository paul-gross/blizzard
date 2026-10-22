"""Git plumbing shared by the workspace bindings (package-private).

Removes the previous tenant's **untracked** files. Ignored files stay
(``tests/test_pin_runner_misc.py`` pins it).
"""

from __future__ import annotations

from pathlib import Path

from blizzard.foundation.logging import get_logger
from blizzard.runner.process.child_env import ChildEnv
from blizzard.runner.process.child_processes import IChildProcesses

_log = get_logger("blizzard.runner.env.git")

# A FILL tick reaches this seam, so git calls must be bounded.
ENV_GIT_TIMEOUT = 60


class EnvGitError(RuntimeError):
    """A git operation the workspace bindings drive failed."""


class SubprocessEnvGit:
    """Run git for the workspace bindings: clean untracked files, read an origin, or capture any call — each under the
    tool profile ``env``, through the launch seam."""

    def __init__(self, *, children: IChildProcesses, env: ChildEnv, timeout: float = ENV_GIT_TIMEOUT) -> None:
        self._children = children
        self._env = env
        self._timeout = timeout

    def clean_environment(self, env_workdir: Path) -> None:
        """``git clean -fd`` every repo worktree under ``env_workdir``."""
        for child in sorted(env_workdir.iterdir()):
            if not (child / ".git").exists():
                continue
            self._git(child, "clean", "-fd")
        _log.info("environment cleaned of untracked files", env_workdir=str(env_workdir))

    def origin_url(self, repo_workdir: Path) -> str:
        """``git remote get-url origin`` read **in the repo's own worktree**.

        Git walks *up* from cwd to find an enclosing repository, so standing anywhere else
        yields a plausible-looking URL for another repo (tests/test_pin_runner_misc.py).
        """
        return self.capture(repo_workdir, "remote", "get-url", "origin").strip()

    def _git(self, cwd: Path, *args: str) -> None:
        self.capture(cwd, *args)

    def capture(self, cwd: Path | None, *args: str) -> str:
        """Run ``git args`` and return its stdout; ``cwd`` is passed as ``-C``, or ``None`` for a call a remote
        answers without any local repository (``ls-remote <url>``)."""
        where = f" in {cwd}" if cwd is not None else ""
        logged_cwd = str(cwd) if cwd is not None else None
        command = ["git", *(["-C", str(cwd)] if cwd is not None else []), *args]
        result = self._children.run(command, cwd=None, env=self._env, timeout=self._timeout)
        if result.timed_out:
            _log.error("git command timed out", args=list(args), cwd=logged_cwd, timeout=self._timeout)
            raise EnvGitError(f"git {' '.join(args)} timed out{where} after {self._timeout}s")
        if result.exit_code != 0:
            detail = (result.stderr or result.stdout).strip()
            _log.error("git command failed", args=list(args), cwd=logged_cwd, detail=detail)
            raise EnvGitError(f"git {' '.join(args)} failed{where}: {detail}")
        return result.stdout
