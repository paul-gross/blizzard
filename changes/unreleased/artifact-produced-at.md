---
kind: added
surfaces: [http-api]
---

Chunk detail artifacts now expose their stored `produced_at` timestamp. For a parked delivery marker, this is the
timestamp used for review expiry; the existing `recorded_at` remains the timestamp decoded from the artifact ID.
