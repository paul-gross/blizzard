"""The OpenCode ``export`` child seam.

Unconfined and time-bounded: it reads already-written session state, not untrusted agent code."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Protocol

from blizzard.runner.process.child_env import ChildEnv
from blizzard.runner.process.child_processes import IChildProcesses

#: Bounds one ``export`` call — a wedged binary costs one failed read, not a hang.
DEFAULT_EXPORT_TIMEOUT_SECONDS = 30.0

#: A failed export's own stderr-tail bound (mirrors ``opencode.adapter``'s idiom, unshared).
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
        self,
        binary: str,
        *,
        children: IChildProcesses,
        env: ChildEnv,
        timeout: float = DEFAULT_EXPORT_TIMEOUT_SECONDS,
    ) -> None:
        self._binary = binary
        self._children = children
        self._env = env
        self._timeout = timeout

    def export(self, session_id: str) -> str:
        with tempfile.TemporaryDirectory(prefix="blizzard-opencode-export-") as scratch:
            out_path = Path(scratch) / "export.json"
            try:
                # A real file, never a pipe: `opencode` exits before a piped stdout write drains,
                # silently truncating a piped capture at the kernel pipe buffer past 64 KiB.
                with out_path.open("wb") as out_file:
                    result = self._children.run(
                        [self._binary, "export", session_id],
                        cwd=None,
                        # `bzh:worker-env-allowlist` — never a full `os.environ` copy into
                        # a plugin-capable third-party CLI.
                        env=self._env,
                        timeout=self._timeout,
                        stdout=out_file,
                    )
            except OSError as exc:
                raise OpenCodeExportError(f"failed to run {self._binary} export {session_id!r}: {exc}") from exc
            if result.timed_out:
                raise OpenCodeExportError(
                    f"failed to run {self._binary} export {session_id!r}: timed out after {self._timeout}s"
                )
            if result.exit_code != 0:
                tail = result.stderr.strip()[-_STDERR_TAIL_BYTES:]
                detail = f" — stderr: {tail}" if tail else ""
                raise OpenCodeExportError(f"{self._binary} export {session_id!r} exited {result.exit_code}{detail}")
            return out_path.read_text()


# Typecheck-time Protocol conformance sentinel (the exemplar's shape): pyright rejects the
# return if `SubprocessOpenCodeExporter` drifts from `IOpenCodeExporter`.
_exporter_conforms: type[IOpenCodeExporter] = SubprocessOpenCodeExporter
