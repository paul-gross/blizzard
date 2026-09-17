"""The one OpenCode CLI command builder (execution spec, D5/"Worker process").

Every non-interactive invocation kind — fresh mint, resume, judgement, and the resume-with-
message send a produces-nudge and a parked-answer delivery both use — composes through
:meth:`OpenCodeCommand.build`, so a flag every one of them requires (``--format json``) or
that only some carry (``--model`` at mint only, ``--variant``/``--auto`` on every one) can
never be reasserted on some kinds and forgotten on others. Interactive takeover is composed
separately (:meth:`OpenCodeCommand.takeover_argv`): it drops ``--format json`` and ``--auto``
entirely, since it is a human at a terminal, never fleet automation — a stated boundary, not
a kind this builder unifies."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OpenCodeInvocationKind(StrEnum):
    """The four non-interactive invocation kinds this one command builder composes, layered
    over the adapter seam's own operations. ``FRESH``/``RESUME`` are both ``spawn`` (mint vs.
    a node-entry resume); ``JUDGE`` is ``judge``; ``NUDGE`` is ``resume_with_message``. A
    produces-nudge and a parked-answer delivery are two distinct CALLER intents that both
    resolve to the exact same ``opencode run --session ... --variant ... --auto`` shape —
    nothing here distinguishes them, so they share ``NUDGE`` rather than gaining a
    same-shaped second member that would carry no information a real caller could act on.

    Interactive takeover is deliberately NOT a fifth member — not an oversight, a stated
    boundary: it drops ``--format json`` and ``--auto`` entirely (a human at a terminal,
    never fleet automation) and is composed by the wholly separate
    :meth:`OpenCodeCommand.takeover_argv`, which takes no ``kind`` at all. Distinct names
    exist for the four kinds here so a caller's intent stays legible even where two of them
    compose identically."""

    FRESH = "fresh"
    RESUME = "resume"
    JUDGE = "judge"
    NUDGE = "nudge"


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
        """The argv for one ``kind`` — never a shell string; every member of
        :class:`OpenCodeInvocationKind` is non-interactive, the takeover paste string is
        composed separately, over :meth:`takeover_argv`, which takes no ``kind`` at all.
        ``kind`` itself drives no branch below — it exists so a call site's intent stays
        legible even where two kinds compose identically (its own docstring).

        ``session_id`` set means resume/judge/nudge: ``--session`` replaces ``--model``
        (mint-only, restored by the session itself on resume — execution spec, "Models,
        effort, permissions, and compaction"). ``variant`` and ``auto`` (unattended
        permission policy) reassert on every kind, mint or resumed alike, since neither is
        session-sticky."""
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

    def takeover_argv(self, *, session_id: str, model: str | None = None, variant: str | None = None) -> list[str]:
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
