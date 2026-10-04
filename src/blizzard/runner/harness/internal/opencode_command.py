"""The one OpenCode CLI command builder (execution spec, "Worker process").

Every non-interactive invocation kind composes through :meth:`OpenCodeCommand.build`, so a
flag every kind requires (``--format json``) or only some carry (``--model`` at mint only)
is never reasserted on some kinds and forgotten on others. Interactive takeover composes
separately (:meth:`OpenCodeCommand.takeover_argv`), dropping those flags entirely."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from blizzard.foundation.roles import domain_model


class OpenCodeInvocationKind(StrEnum):
    """The four non-interactive invocation kinds this one command builder composes.
    ``FRESH``/``RESUME`` are both ``spawn`` (mint vs. resume); ``JUDGE`` is ``judge``;
    ``NUDGE`` is ``resume_with_message``. Interactive takeover is deliberately not a fifth
    member — composed separately by :meth:`OpenCodeCommand.takeover_argv`, kind-less."""

    FRESH = "fresh"
    RESUME = "resume"
    JUDGE = "judge"
    NUDGE = "nudge"


@domain_model
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
        """The argv for one ``kind`` — never a shell string; interactive takeover composes
        separately, over :meth:`takeover_argv`. ``session_id`` set means resume/judge/nudge:
        ``--session`` replaces ``--model`` (mint-only; restored by the session itself on
        resume — execution spec). ``variant``/``auto`` reassert on every kind, since
        neither is session-sticky."""
        cmd = [self.binary, "run", "--format", "json"]
        if session_id:
            cmd += ["--session", session_id]
        elif model:
            cmd += ["--model", model]
        if variant:
            cmd += ["--variant", variant]
        if auto:
            # `--auto` approves what the runner-owned config does not explicitly deny
            # — never a CLI-composed deny rule; those live in that config alone.
            cmd.append("--auto")
        cmd.append(prompt)
        return cmd

    def takeover_argv(self, *, session_id: str, model: str | None = None, variant: str | None = None) -> list[str]:
        """The exec'd interactive TUI argv — no ``--format json``, no
        ``--auto``: an attended session is a human at a terminal who approves tool use
        live, never fleet automation."""
        cmd = [self.binary, "--session", session_id]
        if model:
            cmd += ["--model", model]
        if variant:
            cmd += ["--variant", variant]
        return cmd


__all__ = ["OpenCodeCommand", "OpenCodeInvocationKind"]
