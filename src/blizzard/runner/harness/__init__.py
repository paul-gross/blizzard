"""The harness domain — the coding-harness adapter seam.

Blizzard is coding-harness-agnostic: every harness sits behind one small adapter
(:mod:`.adapter`). Adapters stay **dumb** — they translate, they never decide
(``bzh:deterministic-shell``); reference bindings live under ``internal/``."""

from __future__ import annotations
