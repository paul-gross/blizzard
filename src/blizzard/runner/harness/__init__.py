"""The harness domain — the coding-harness adapter seam (D-025/D-092).

Blizzard is coding-harness-agnostic: it drives Claude Code today, OpenCode and
Codex as they mature, all behind one small adapter (:mod:`.adapter`). Adapters stay
**dumb** — they translate, they never decide; all arbitration lives in the
deterministic core (``bzh:deterministic-shell``). Reference bindings live under
``internal/`` (``bzh:pluggable-seams``); in verification the binding is the
``blizzard-mock`` mock-claude-code façade, which makes real commits from a scripted
prompt (``verification.md``).
"""

from __future__ import annotations
