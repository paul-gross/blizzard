"""The OpenCode permission-resolution subprocess seam.

Asks OpenCode itself what an unattended launch's effective permissions are — built-in defaults, user and
project config, ``.opencode/`` agents, and env layers included — rather than re-implementing its layering.
A bare, Landlock-free ``subprocess.run`` like :mod:`opencode_export`: it reads resolved configuration, it
does not run agent code."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from blizzard.runner.harness.internal.opencode_shapes import (
    OpenCodePermissionRule,
    OpenCodeShapeError,
    parse_agent_rulesets,
    parse_resolved_config,
)

#: Bounds one resolver call — a wedged binary costs one failed launch, not a hang.
DEFAULT_RESOLVE_TIMEOUT_SECONDS = 30.0

_STDERR_TAIL_BYTES = 2000


class OpenCodePermissionResolveError(RuntimeError):
    """Raised when OpenCode's effective permissions cannot be established."""


@dataclass(frozen=True)
class OpenCodeEffectivePermissions:
    """The merged configuration plus every agent's ordered resolved ruleset."""

    merged_config: Mapping[str, Any]
    agent_rulesets: Mapping[str, tuple[OpenCodePermissionRule, ...]]


class IOpenCodePermissionResolver(Protocol):
    """The one-method resolution seam (``bzh:seam-size-ceiling``)."""

    def resolve(self, *, cwd: str, env: Mapping[str, str]) -> OpenCodeEffectivePermissions:
        """Effective permissions for a launch in ``cwd`` under ``env``. Raises
        :class:`OpenCodePermissionResolveError` on any failure — never returns a partial answer."""
        ...


class SubprocessOpenCodePermissionResolver:
    """Runs ``<binary> debug config`` and ``<binary> agent list`` under the launch cwd and env."""

    def __init__(self, binary: str, *, timeout: float = DEFAULT_RESOLVE_TIMEOUT_SECONDS) -> None:
        self._binary = binary
        self._timeout = timeout

    def resolve(self, *, cwd: str, env: Mapping[str, str]) -> OpenCodeEffectivePermissions:
        config_text = self._run(["debug", "config"], cwd, env)
        agents_text = self._run(["agent", "list"], cwd, env)
        try:
            return OpenCodeEffectivePermissions(
                merged_config=parse_resolved_config(config_text), agent_rulesets=parse_agent_rulesets(agents_text)
            )
        except OpenCodeShapeError as exc:
            raise OpenCodePermissionResolveError(f"unreadable {self._binary} permission output: {exc}") from exc

    def _run(self, args: list[str], cwd: str, env: Mapping[str, str]) -> str:
        try:
            result = subprocess.run(
                [self._binary, *args],
                cwd=cwd,
                # The launch env is already the allowlisted one (`bzh:worker-env-allowlist`).
                env=dict(env),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=self._timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OpenCodePermissionResolveError(f"failed to run {self._binary} {' '.join(args)}: {exc}") from exc
        if result.returncode != 0:
            tail = (result.stderr or "").strip()[-_STDERR_TAIL_BYTES:]
            detail = f" — stderr: {tail}" if tail else ""
            raise OpenCodePermissionResolveError(f"{self._binary} {' '.join(args)} exited {result.returncode}{detail}")
        return result.stdout


# Typecheck-time Protocol conformance sentinel (the exemplar's shape).
def _conforms_resolver(x: SubprocessOpenCodePermissionResolver) -> IOpenCodePermissionResolver:
    return x
