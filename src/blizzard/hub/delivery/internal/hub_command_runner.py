"""The subprocess-backed hub command runner (``bzh:pluggable-seams``).

The one place ``subprocess`` runs on the hub. Confined to ``internal/`` (adapter
placement, ``bzh:dependency-inversion``); the domain sees only
:class:`~blizzard.hub.delivery.command_runner.IHubCommandRunner`.
"""

from __future__ import annotations

import os
import subprocess
import sys

from blizzard.hub.delivery.command_runner import CommandResult, IHubCommandRunner


class SubprocessHubCommandRunner:
    """Runs a hub command node's declared command via ``subprocess.run``."""

    def __init__(self, *, timeout: float | None = None) -> None:
        self._timeout = timeout

    def run(self, *, command: str, cwd: str, env: dict[str, str]) -> CommandResult:
        # Merge onto the hub daemon's own environment — never a bare replacement —
        # mirroring the worker spawn's own env-build (`_spawn_env`,
        # `runner/harness/internal/claude_code_adapter.py`): a `run:` script needs
        # ``PATH``/``PYTHONPATH``/``VIRTUAL_ENV`` etc. to resolve ``git``/``python3``/
        # the ``blizzard`` package the same way the hub process itself does — the
        # node-specific ``BZ_*`` keys are added on top (never removed by the parent
        # env), so a script sees both. Inheriting ``PATH`` alone is not enough for
        # ``python3``: a wheel-installed daemon launched by absolute path (systemd)
        # inherits a PATH with no venv on it, so a bare ``python3`` in a ``run:``
        # step would resolve to an interpreter that cannot import ``blizzard`` —
        # prepend the hub interpreter's own bin dir so it always resolves to the
        # interpreter the hub itself runs under (``bzh:hub-node-env-contract``).
        full_env = {**os.environ, **env}
        interpreter_bin = os.path.dirname(sys.executable)
        path = full_env.get("PATH", "")
        if interpreter_bin and path.split(os.pathsep, 1)[0] != interpreter_bin:
            full_env["PATH"] = f"{interpreter_bin}{os.pathsep}{path}" if path else interpreter_bin
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                env=full_env,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return CommandResult(exit_code=124, stdout=stdout, stderr=f"{stderr}\ntimed out after {self._timeout}s")
        return CommandResult(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)


def _conforms_hub_command_runner(x: SubprocessHubCommandRunner) -> IHubCommandRunner:
    return x
