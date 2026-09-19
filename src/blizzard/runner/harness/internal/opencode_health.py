"""The OpenCode health-probe binding (blizzard#438).

Standalone, like :class:`~blizzard.runner.harness.internal.opencode_probe.
OpenCodeCompatibilityProbe`: the health-probe seam is a narrow, separately-injected
collaborator rather than a slice folded onto :class:`~blizzard.runner.harness.internal.
opencode_adapter.OpenCodeAdapter` itself. Nothing composes it yet (blizzard#438 phase 1)."""

from __future__ import annotations

import os
from pathlib import Path

from blizzard.runner.harness.adapter import IHarnessHealthProbe
from blizzard.runner.harness.compatibility import CompatibilityProbe
from blizzard.runner.harness.health import DeclaredDegradation
from blizzard.runner.harness.internal import harness_shared
from blizzard.runner.harness.internal.opencode_probe import PINNED_OPENCODE_VERSION

# The three `compatibility.py::DEGRADABLE_ABSENCES` members OpenCode 1.18.25 is known to
# leave absent (`contracts/opencode/1.18.25/manifest.json`'s own diagnostic fixture names
# `root_hook` and `child_sessions` degraded; `usage_cost` is degradable by the same closed
# policy even where one particular fixture happened to observe it) — declared statically,
# not re-derived from a live corpus read, since this is what the binding declares about
# itself independent of any one observation.
_OPENCODE_DEGRADATIONS: tuple[DeclaredDegradation, ...] = (
    DeclaredDegradation(
        probe=CompatibilityProbe.ROOT_HOOK,
        summary=(
            "OpenCode has no portable root-hook lifecycle signal to observe, so the runner can "
            "never confirm whether its plugin's heartbeat nudge and per-tool identity forwarding "
            "actually loaded (docs/deployment/worker-spawn.md)."
        ),
    ),
    DeclaredDegradation(
        probe=CompatibilityProbe.USAGE_COST,
        summary=(
            "a turn can export token usage with no cost figure attached, understating a session's "
            "spend even though the turn itself completed."
        ),
    ),
    DeclaredDegradation(
        probe=CompatibilityProbe.CHILD_SESSIONS,
        summary=(
            "the pinned build denies the `task` tool for every agent, so no run can prove or "
            "disprove that a spawned child session reports its parent linkage correctly."
        ),
    ),
)


def _default_opencode_auth_path() -> Path:
    """Where OpenCode's own credential discovery reads from — mirrors
    ``opencode_scratch_config.py``'s ``auth_path`` (``<data-home>/opencode/auth.json``),
    the same path the compatibility proof's disposable-auth provisioning copies from."""
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data_home) / "opencode" / "auth.json"


class OpenCodeHealthProbe:
    """The OpenCode binding's :class:`~blizzard.runner.harness.adapter.IHarnessHealthProbe`.
    Dumb, like the adapter it stands beside: reports evidence, never decides availability."""

    def __init__(self, binary: str = "opencode", *, auth_path: str | None = None) -> None:
        self._binary = binary
        # Injectable for testability (`bzh:dependency-injection`); defaults to OpenCode's
        # own real credential-discovery path.
        self._auth_path = Path(auth_path) if auth_path is not None else _default_opencode_auth_path()

    def binary_present(self) -> bool:
        return harness_shared.binary_present(self._binary)

    def probe_authentication(self) -> bool:
        """OpenCode exposes no subcommand that reports provider-authentication status
        without spending a live turn, so this combines the two free, local signals
        available instead: the binary must still answer ``--version`` (proving it
        launches in this environment, not merely that it resolves on ``PATH``), and
        OpenCode's own credential-discovery path must hold a non-empty document.
        Neither call reaches a provider, and neither proves a held credential is still
        valid — a later phase with a live-provider budget should replace this with a
        genuine round trip."""
        if harness_shared.observe_version(self._binary) is None:
            return False
        try:
            return self._auth_path.stat().st_size > 0
        except OSError:
            return False

    def supported_version(self) -> str | None:
        return PINNED_OPENCODE_VERSION

    def declared_degradations(self) -> tuple[DeclaredDegradation, ...]:
        return _OPENCODE_DEGRADATIONS


def _conforms_harness_health_probe(x: OpenCodeHealthProbe) -> IHarnessHealthProbe:
    return x


__all__ = ["OpenCodeHealthProbe"]
