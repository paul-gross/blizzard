"""The process work the OpenCode 2 proof needs beyond the service's API."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class CommandResult:
    exit_code: int | None
    stdout: str
    stderr: str


class IProofHost(Protocol):
    def run(self, argv: Sequence[str], *, cwd: Path, timeout: float, home: Path | None = None) -> CommandResult:
        """Run under the runner's tool profile, which carries no lease identity. With ``home``, every OpenCode
        directory — config, data, state, cache — is rooted there instead of the operator's."""
        ...

    def attend(self, argv: Sequence[str], *, cwd: Path, keystrokes: str, settle_seconds: float) -> None:
        """Open ``argv`` on a terminal, type ``keystrokes``, wait ``settle_seconds``, and end it."""
        ...

    def process_alive(self, pid: int) -> bool: ...


__all__ = ["CommandResult", "IProofHost"]
