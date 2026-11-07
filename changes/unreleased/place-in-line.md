---
kind: added
surfaces: [http-api]
---

Says whether a place holds a position in its flow's line. Each place on a flow's line, and each of a chunk's flow
places, gains `in_line`: true for `next`, `waiting`, and `polling`, false for `landing`, `awaiting-merge`, and
`passed-over`.
