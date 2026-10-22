"""The ``PATH`` and ``--version`` probes behind each binding's health and version reads — the
launch-seam driver the harness bindings share (``bzh:pluggable-seams``)."""

from __future__ import annotations

import shutil

from blizzard.foundation.logging import get_logger
from blizzard.runner.process.child_env import ChildEnv
from blizzard.runner.process.child_processes import IChildProcesses

_log = get_logger("blizzard.runner.harness")

# Bounds `observe_version`'s probe: a wedged binary costs one skipped read, not a hang.
VERSION_PROBE_TIMEOUT_SECONDS = 5


class VersionProbe:
    """Runs a harness binary's ``--version`` through the launch seam under the tool profile ``env``."""

    def __init__(self, children: IChildProcesses, env: ChildEnv) -> None:
        self._children = children
        self._env = env

    def binary_present(self, binary: str) -> bool:
        """Whether ``binary`` resolves right now on the tool profile's ``PATH`` — the one its child launches under,
        not the daemon's — for a caller that needs presence without a version probe."""
        return shutil.which(binary, path=self._env.variables.get("PATH", "")) is not None

    def observe_version(self, binary: str) -> str | None:
        """The configured executable's version, observed right now — bounded and non-raising:
        a timeout, a missing binary, or empty output all read as ``None``, logged rather than
        propagated. Uncached; identical for every binding, only ``binary`` differs. Absent from
        ``PATH`` entirely (one of several known bindings, unconfigured here) skips the launch
        and logs at ``debug``, not the genuine-failure ``warning``."""
        if not self.binary_present(binary):
            _log.debug("harness binary not found on PATH; skipping version probe", binary=binary)
            return None
        try:
            result = self._children.run(
                [binary, "--version"], cwd=None, env=self._env, timeout=VERSION_PROBE_TIMEOUT_SECONDS
            )
        except OSError as exc:
            _log.warning("harness version probe failed", binary=binary, detail=str(exc))
            return None
        if result.timed_out:
            _log.warning("harness version probe failed", binary=binary, detail="timed out")
            return None
        return result.stdout.strip() or result.stderr.strip() or None
