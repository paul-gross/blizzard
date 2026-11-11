---
kind: fixed
---

OpenCode keeps the config snapshot the runner started with. Before, a bundle published after startup moved OpenCode onto
the new snapshot while Claude Code stayed on the old one. `blizzard runner transcript backfill` and `reship` on a runner
with a `[harness] config_dir` now use the already-published snapshot, and refuse when none is published.
