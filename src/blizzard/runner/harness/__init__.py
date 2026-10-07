"""The harness domain — the coding-harness adapter seam.

Blizzard is coding-harness-agnostic: every harness sits behind one small adapter (:mod:`.adapter`).
Adapters stay **dumb** — they translate, they never decide (``bzh:deterministic-shell``). The reference
bindings are the adapter packages :mod:`.claude_code` and :mod:`.opencode`; within ``harness/`` only
:mod:`.wiring` names them."""

from __future__ import annotations
