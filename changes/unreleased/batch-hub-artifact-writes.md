---
kind: changed
surfaces: [store]
---

A hub write that records several artifacts now reads the chunk's artifact order once and inserts them together, and the
open-escalations feed loads only the fact families it reads, so each board poll and chunk write issues fewer statements.
