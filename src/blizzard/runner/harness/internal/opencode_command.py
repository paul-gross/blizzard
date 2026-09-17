"""The one OpenCode CLI command builder (execution spec, D5/"Worker process").

Every non-interactive invocation kind the spec names — fresh mint, resume, judgement, nudge,
and answer delivery — composes through :meth:`OpenCodeCommand.build`, so a flag every one of
them requires (``--format json``) or that only some carry (``--model`` at mint only,
``--variant``/``--auto`` on every one) can never be reasserted on some kinds and forgotten on
others. Interactive takeover is composed separately (:meth:`OpenCodeCommand.takeover_argv`):
it drops ``--format json`` and ``--auto`` entirely, since it is a human at a terminal, never
fleet automation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OpenCodeInvocationKind(StrEnum):
    """The six invocation kinds the execution spec names, layered over the adapter seam's
    four operations. ``FRESH``/``RESUME`` are both ``spawn`` (mint vs. a node-entry resume);
    ``JUDGE`` is ``judge``; ``NUDGE``/``ANSWER`` are both ``resume_with_message`` (a
    produces-nudge and a parked-answer delivery share one adapter call); ``TAKEOVER`` is
    ``resume_command``. Distinct names exist so a caller's intent stays legible even where
    two kinds compose identically."""

    FRESH = "fresh"
    RESUME = "resume"
    JUDGE = "judge"
    NUDGE = "nudge"
    ANSWER = "answer"
    TAKEOVER = "takeover"


# Every kind but TAKEOVER runs the non-interactive `opencode run --format json` form.
_NON_INTERACTIVE_KINDS = frozenset(
    {
        OpenCodeInvocationKind.FRESH,
        OpenCodeInvocationKind.RESUME,
        OpenCodeInvocationKind.JUDGE,
        OpenCodeInvocationKind.NUDGE,
        OpenCodeInvocationKind.ANSWER,
    }
)


@dataclass(frozen=True)
class OpenCodeCommand:
    """Composes every ``opencode`` invocation from one place — the binary path is the only
    thing every kind shares unconditionally."""

    binary: str

    def build(
        self,
        kind: OpenCodeInvocationKind,
        *,
        prompt: str,
        session_id: str | None = None,
        model: str | None = None,
        variant: str | None = None,
        auto: bool = False,
    ) -> list[str]:
        """The argv for one non-interactive ``kind`` — never a shell string; the takeover
        paste string is composed separately, over :meth:`takeover_argv`.

        ``session_id`` set means resume/judge/nudge/answer: ``--session`` replaces
        ``--model`` (mint-only, restored by the session itself on resume — execution spec,
        "Models, effort, permissions, and compaction"). ``variant`` and ``auto`` (unattended
        permission policy) reassert on every kind, mint or resumed alike, since neither is
        session-sticky."""
        if kind not in _NON_INTERACTIVE_KINDS:
            raise ValueError(f"{kind} is not a non-interactive invocation kind")
        cmd = [self.binary, "run", "--format", "json"]
        if session_id:
            cmd += ["--session", session_id]
        elif model:
            cmd += ["--model", model]
        if variant:
            cmd += ["--variant", variant]
        if auto:
            # `--auto` approves what the runner-owned config does not explicitly deny
            # (D7) — never a CLI-composed deny rule; those live in that document alone.
            cmd.append("--auto")
        cmd.append(prompt)
        return cmd

    def takeover_argv(
        self, *, session_id: str, model: str | None = None, variant: str | None = None
    ) -> list[str]:
        """The exec'd interactive TUI argv (issue #258) — no ``--format json``, no
        ``--auto``: an attended session is a human at a terminal who approves tool use
        live, never fleet automation."""
        cmd = [self.binary, "--session", session_id]
        if model:
            cmd += ["--model", model]
        if variant:
            cmd += ["--variant", variant]
        return cmd


__all__ = ["OpenCodeCommand", "OpenCodeInvocationKind"]
