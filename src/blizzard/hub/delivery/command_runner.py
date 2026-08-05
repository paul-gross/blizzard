"""The hub command-runner seam (#65) — one shell command at a time, the *mechanism* behind a hub
command node's ``run:`` list.

Its reference binding (:mod:`~blizzard.hub.delivery.internal.hub_command_runner`) is the one place
``subprocess`` runs on the hub (``bzh:dependency-inversion``, ``bzh:domain-core``). Structurally
agentless (``bzh:deterministic-shell``): the env passed in never carries a model credential."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CommandResult:
    """One command's outcome — the runner's raw report, before outcome mapping."""

    exit_code: int
    stdout: str
    stderr: str


class IHubCommandRunner(Protocol):
    """Runs one declared command with an injected env and working directory."""

    def run(self, *, command: str, cwd: str, env: dict[str, str]) -> CommandResult:
        """Execute ``command`` (a shell command line) in ``cwd`` with exactly ``env``."""
        ...
