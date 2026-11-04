---
kind: fixed
surfaces: [cli]
---

`blizzard runner opencode drive` now reads every page of a session's messages, where it read only the first page, and
reads a step that is still streaming instead of counting it as a failed poll, so a long tool call no longer runs the
turn into its failure bound.
