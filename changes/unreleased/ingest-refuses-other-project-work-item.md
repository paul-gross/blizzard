---
kind: changed
surfaces: [http-api]
---

Chunk ingest answers `422` when a hub work item it wraps is recorded under a project other than the one the chunk is
ingested into, so a chunk's work items stay in one project.
