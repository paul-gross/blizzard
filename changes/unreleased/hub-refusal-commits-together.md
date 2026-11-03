---
kind: fixed
surfaces: [event-stream, board]
---

A hub visit the hub refuses — its repositories do not resolve, a repository secret is unavailable, or its answer routes
nowhere — now records its `hub-log` artifact, its event, and its routing together or not at all. A refusal interrupted
part way no longer leaves the chunk routed on with no `repository-unresolved` (or `hub-node-unroutable-outcome`) event
in the event log and no `event-logged` frame on the stream; the next pass records the refusal whole, with its one event.
