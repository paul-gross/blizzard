---
kind: fixed
surfaces: [http-api, cli]
---

A finding verb that retries after a concurrent verb on the same finding now records its fact at the moment it
writes, so it reads as the newest fact and the finding takes the state it set, instead of keeping the concurrent
verb's state. A garden delivery's facts on existing findings are timed the same way.
