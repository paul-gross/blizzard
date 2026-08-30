"""The judgement prompt a dead worker is resumed with to elicit its verdict."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.runner.domain.checks import CheckResultRecord
from blizzard.wire.envelope import NodeEnvelope


@dataclass(frozen=True)
class JudgementPrompt:
    """One attempt's prompt, over the node and the checks run at its worker's exit."""

    envelope: NodeEnvelope
    check_results: list[CheckResultRecord]

    def render(self) -> str:
        """Authored prose, then the checks, then the elicitation.

        The checks ride between so the worker judges against mechanical truth.
        """
        return (self.envelope.judgement_prompt or "") + self._checks_block() + self._elicitation_tail()

    def _checks_block(self) -> str:
        """One line per check with its command and ``PASS``/``FAIL`` (issue #114).

        A failed check additionally shows its output tail; a node with no checks adds nothing.
        """
        if not self.check_results:
            return ""
        lines = ["", "", "# Checks (runner-executed at your exit — judge against these, not your recollection):"]
        for r in self.check_results:
            lines.append(f"#   [{'PASS' if r.passed else 'FAIL'}] {r.command}")
            if not r.passed:
                for tail_line in r.output_tail.strip().splitlines():
                    lines.append(f"#       {tail_line}")
        return "\n".join(lines)

    def _elicitation_tail(self) -> str:
        """The engine-generated ``<Choice>`` elicitation; same inert ``#`` framing as the resume messages."""
        lines = ["", "", "# Select exactly one outcome and reply with <Choice>name</Choice>:"]
        for choice in self.envelope.node.choices:
            lines.append(f"#   - {choice.name}: {choice.description}")
        return "\n".join(lines)
