---
kind: added
surfaces: [cli]
---

`blizzard hub chunk transcript list|get` and `blizzard runner transcript list|get` read a chunk's recorded session
transcript in the terminal: `list` shows a chunk's segments, `get` prints one segment turn by turn as the board shows
it, and `--json` prints the body unchanged. The segment ids `list` prints are the ones `transcript reship` takes.
