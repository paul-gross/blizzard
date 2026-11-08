---
kind: fixed
surfaces: [event-stream]
---

A launch OpenCode 2 refused because its service changed version spent a retry each time, until the chunk ran out of
retries. The runner now relaunches once on the version it just observed; a version it cannot hold escalates the chunk
`harness-version-changed` without spending a retry. Restart the runner on a supported release, then requeue.
