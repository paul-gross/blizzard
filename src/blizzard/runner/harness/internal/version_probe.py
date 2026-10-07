"""The ``PATH`` and ``--version`` probes behind each binding's health and version reads — the
subprocess driver the harness bindings share (``bzh:pluggable-seams``)."""

from __future__ import annotations

import shutil
import subprocess

from blizzard.foundation.logging import get_logger

_log = get_logger("blizzard.runner.harness")

# Bounds `observe_version`'s probe: a wedged binary costs one skipped read, not a hang.
VERSION_PROBE_TIMEOUT_SECONDS = 5


def binary_present(binary: str) -> bool:
    """Whether ``binary`` resolves on ``PATH`` right now — bounded and non-raising, the
    standalone half of :func:`observe_version`'s own internal presence check, for a caller
    (the health-probe seam) that needs presence without paying for a version probe."""
    return shutil.which(binary) is not None


def observe_version(binary: str) -> str | None:
    """The configured executable's version, observed right now — bounded and non-raising:
    a timeout, a missing binary, or empty output all read as ``None``, logged rather than
    propagated. Uncached; identical for every binding, only ``binary`` differs. Absent from
    ``PATH`` entirely (one of several known bindings, unconfigured here) skips the subprocess
    and logs at ``debug``, not the genuine-failure ``warning``."""
    if not binary_present(binary):
        _log.debug("harness binary not found on PATH; skipping version probe", binary=binary)
        return None
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=VERSION_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.warning("harness version probe failed", binary=binary, detail=str(exc))
        return None
    return result.stdout.strip() or result.stderr.strip() or None
