---
kind: fixed
surfaces: [event-stream, board]
---

A hub turn's live `chunk-changed` frame now carries the key and cause the activity feed gives the fact the turn wrote
(`transitions:…` with `hub-advanced`, `escalations:…` with `escalated`, `chunk_migrations:…` with `migrated`), so the
board shows each such move as one row rather than a live row and a second one after a refetch.
