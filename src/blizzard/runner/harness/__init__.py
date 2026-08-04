"""The harness domain — the coding-harness adapter seam.

Blizzard is coding-harness-agnostic: it drives Claude Code today, OpenCode and
Codex as they mature, all behind one small adapter (:mod:`.adapter`). Adapters stay
**dumb** — they translate, they never decide; all arbitration lives in the
deterministic core (``bzh:deterministic-shell``). Reference bindings live under
``internal/`` (``bzh:pluggable-seams``).
"""

from __future__ import annotations
