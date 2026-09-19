"""The OpenCode ``export`` subprocess seam (blizzard#437).

A bare, Landlock-free ``subprocess.run`` — this reads already-written session state, not
untrusted agent code, so it owns none of ``opencode_process.py``'s diagnostic-only confinement
machinery (that module's own docstring forbids production reuse). Mirrors
``harness_shared.observe_version``'s own bounded, non-Landlocked subprocess idiom."""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from blizzard.runner.harness.env_allowlist import AllowlistedEnv

#: Bounds one ``export`` call — a wedged binary costs one failed read, not a hang.
DEFAULT_EXPORT_TIMEOUT_SECONDS = 30.0

#: A failed export's own stderr-tail bound (mirrors ``opencode_adapter``'s idiom, unshared).
_STDERR_TAIL_BYTES = 2000


class OpenCodeExportError(RuntimeError):
    """Raised on any failure to obtain ``opencode export``'s output: a missing binary, a
    timeout, or a non-zero exit."""


class IOpenCodeExporter(Protocol):
    """The one-method export seam (``bzh:seam-size-ceiling``)."""

    def export(self, session_id: str) -> str:
        """``session_id``'s raw ``opencode export`` stdout (JSON text). Raises
        :class:`OpenCodeExportError` on any failure — never returns partial output."""
        ...


class SubprocessOpenCodeExporter:
    """Runs ``<binary> export <session-id>`` once per call, with no cwd — the spec's own
    export resolves a session by id from any working directory."""

    def __init__(
        self, binary: str, *, env_passthrough: Sequence[str] = (), timeout: float = DEFAULT_EXPORT_TIMEOUT_SECONDS
    ) -> None:
        self._binary = binary
        self._env_passthrough = tuple(env_passthrough)
        self._timeout = timeout

    def export(self, session_id: str) -> str:
        with tempfile.TemporaryDirectory(prefix="blizzard-opencode-export-") as scratch:
            out_path = Path(scratch) / "export.json"
            try:
                # A real file, never a pipe: `opencode` exits before a piped stdout write drains,
                # silently truncating a piped capture at the kernel pipe buffer past 64 KiB.
                with out_path.open("w") as out_file:
                    result = subprocess.run(
                        [self._binary, "export", session_id],
                        # `bzh:worker-env-allowlist` — never a full `os.environ` copy into
                        # a plugin-capable third-party CLI.
                        env=AllowlistedEnv.of(self._env_passthrough).variables,
                        stdout=out_file,
                        stderr=subprocess.PIPE,
                        text=True,
                        check=False,
                        timeout=self._timeout,
                    )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise OpenCodeExportError(f"failed to run {self._binary} export {session_id!r}: {exc}") from exc
            if result.returncode != 0:
                tail = (result.stderr or "").strip()[-_STDERR_TAIL_BYTES:]
                detail = f" — stderr: {tail}" if tail else ""
                raise OpenCodeExportError(f"{self._binary} export {session_id!r} exited {result.returncode}{detail}")
            return out_path.read_text()


# Typecheck-time Protocol conformance sentinel (the exemplar's shape): pyright rejects the
# return if `SubprocessOpenCodeExporter` drifts from `IOpenCodeExporter`.
def _conforms_exporter(x: SubprocessOpenCodeExporter) -> IOpenCodeExporter:
    return x
