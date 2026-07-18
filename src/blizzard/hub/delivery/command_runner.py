"""The hub command-runner seam (#65).

A generic hub command node's ``run:`` list is a list of shell commands the hub
executes itself — the *mechanism* behind the ``HubNodeExecutor``'s *policy*
(``bzh:dependency-inversion``): the executor never imports ``subprocess`` directly
(``bzh:domain-core``), it drives one command at a time through this Protocol. The
reference binding (:mod:`~blizzard.hub.delivery.internal.hub_command_runner`) is the
one place ``subprocess`` runs on the hub; tests bind a fake returning scripted
``(exit_code, stdout, stderr)`` triples.

Structurally agentless (``bzh:deterministic-shell``): the env a caller builds and
passes in never carries a model credential — see
:mod:`~blizzard.hub.delivery.hub_node`'s env-injection contract.
"""

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
