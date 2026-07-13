"""The Claude Code adapter binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.runner.harness.adapter.IHarnessAdapter` against the
``claude`` CLI — ``claude -p --output-format json --session-id <sid> --settings
<worker-settings>`` for spawn, ``claude -p --resume <sid>`` for the automated
follow-up, ``claude --resume <sid>`` for the interactive takeover (D-092/harness-
adapters.md). In verification the ``binary`` points at the ``blizzard-mock``
mock-claude-code façade, which makes real commits from a scripted prompt
(``verification.md``). Confined to ``internal/`` (``bzh:dependency-inversion``).

**P6 contract stub.** Methods raise :class:`NotImplementedError`; the walking-
skeleton runner-track builder wires them (the binary path is configurable so the
mock façade binds in tests and the real ``claude`` in production).
"""

from __future__ import annotations

from blizzard.runner.harness.adapter import (
    IHarnessAdapter,
    WorkerHandle,
    WorkerPreamble,
)
from blizzard.wire.envelope import NodeEnvelope

_UNIMPLEMENTED = "Claude Code adapter lands in the P6 walking skeleton"


class ClaudeCodeAdapter:
    """The Claude Code binding — a NotImplemented stub until P6 wires the CLI.

    ``binary`` is configurable (D-092): the mock-claude-code façade in verification,
    the real ``claude`` in production — the seam does not change.
    """

    def __init__(self, binary: str = "claude") -> None:
        self._binary = binary

    def spawn(self, envelope: NodeEnvelope, preamble: WorkerPreamble, session_hint: str | None) -> WorkerHandle:
        raise NotImplementedError(_UNIMPLEMENTED)

    def resume_with_message(self, environment_id: str, session_id: str, message: str) -> int:
        raise NotImplementedError(_UNIMPLEMENTED)

    def resume_command(self, environment_id: str, session_id: str) -> str:
        raise NotImplementedError(_UNIMPLEMENTED)

    def parse_verdict(self, output: str) -> str | None:
        raise NotImplementedError(_UNIMPLEMENTED)


def _conforms_harness_adapter(x: ClaudeCodeAdapter) -> IHarnessAdapter:
    return x
