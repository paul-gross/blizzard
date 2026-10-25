---
kind: added
surfaces: [http-api]
---

The hub's and the runner's `GET /api/events/stream` accept `?from=latest`: the stream skips the replay ring and sends
only the events published after it joined. A readable `Last-Event-ID` still resumes; an unknown `from` value, or
`from=latest` with `?last_event_id=`, answers `422`.
