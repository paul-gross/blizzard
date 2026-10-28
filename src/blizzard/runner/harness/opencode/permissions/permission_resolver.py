"""The OpenCode permission-resolution child seam.

Asks OpenCode itself what an unattended launch's effective permissions are — built-in defaults, user and
project config, ``.opencode/`` agents, and env layers included.
A bare, Landlock-free run through the launch seam like :mod:`opencode.transcript.export`: it reads resolved
configuration, it does not run agent code."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.json_value import JsonValue
from blizzard.runner.harness.opencode.shapes import (
    OpenCodePermissionRule,
    OpenCodeShapeError,
    parse_agent_rulesets,
    parse_resolved_config,
)
from blizzard.runner.process.child_env import ChildEnv
from blizzard.runner.process.child_processes import IChildProcesses

#: Bounds one resolver call — a wedged binary costs one failed launch, not a hang.
DEFAULT_RESOLVE_TIMEOUT_SECONDS = 30.0

_STDERR_TAIL_BYTES = 2000


class OpenCodePermissionResolveError(RuntimeError):
    """Raised when OpenCode's effective permissions cannot be established."""


@domain_model
@dataclass(frozen=True)
class OpenCodeEffectivePermissions:
    """The merged configuration plus every agent's ordered resolved ruleset."""

    merged_config: Mapping[str, JsonValue]
    agent_rulesets: Mapping[str, tuple[OpenCodePermissionRule, ...]]


class IOpenCodePermissionResolver(Protocol):
    """The one-method resolution seam (``bzh:seam-size-ceiling``)."""

    def resolve(self, *, cwd: str, env: ChildEnv) -> OpenCodeEffectivePermissions:
        """Effective permissions for a launch in ``cwd`` under ``env``. Raises
        :class:`OpenCodePermissionResolveError` on any failure — never returns a partial answer."""
        ...


class SubprocessOpenCodePermissionResolver:
    """Runs ``<binary> debug config`` and ``<binary> agent list`` under the launch cwd and env."""

    def __init__(
        self, binary: str, *, children: IChildProcesses, timeout: float = DEFAULT_RESOLVE_TIMEOUT_SECONDS
    ) -> None:
        self._binary = binary
        self._children = children
        self._timeout = timeout

    def resolve(self, *, cwd: str, env: ChildEnv) -> OpenCodeEffectivePermissions:
        config_text = self._run(["debug", "config"], cwd, env)
        agents_text = self._run(["agent", "list"], cwd, env)
        try:
            return OpenCodeEffectivePermissions(
                merged_config=parse_resolved_config(config_text), agent_rulesets=parse_agent_rulesets(agents_text)
            )
        except OpenCodeShapeError as exc:
            raise OpenCodePermissionResolveError(f"unreadable {self._binary} permission output: {exc}") from exc

    def _run(self, args: list[str], cwd: str, env: ChildEnv) -> str:
        try:
            # The launch env is already the allowlisted one (`bzh:worker-env-allowlist`).
            result = self._children.run([self._binary, *args], cwd=cwd, env=env, timeout=self._timeout)
        except OSError as exc:
            raise OpenCodePermissionResolveError(f"failed to run {self._binary} {' '.join(args)}: {exc}") from exc
        if result.timed_out:
            raise OpenCodePermissionResolveError(
                f"failed to run {self._binary} {' '.join(args)}: timed out after {self._timeout}s"
            )
        if result.exit_code != 0:
            tail = result.stderr.strip()[-_STDERR_TAIL_BYTES:]
            detail = f" — stderr: {tail}" if tail else ""
            raise OpenCodePermissionResolveError(f"{self._binary} {' '.join(args)} exited {result.exit_code}{detail}")
        return result.stdout


# Typecheck-time Protocol conformance sentinel (the exemplar's shape).
_resolver_conforms: type[IOpenCodePermissionResolver] = SubprocessOpenCodePermissionResolver
