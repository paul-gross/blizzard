---
kind: fixed
surfaces: [config]
---

Claude Code's transcripts are read from the worker's Claude config home when `transcripts_root` is unset. With
`CLAUDE_CONFIG_DIR` passed through to workers, the transcript reader and the ambient settings check now look under the
same directory; before, the reader looked under the daemon's own `~/.claude/projects` and read those sessions as gone.
