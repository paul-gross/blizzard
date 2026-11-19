---
kind: changed
surfaces: [config]
---

The hub's `[transcripts]` keys `record_max_bytes`, `chunk_budget_max_bytes`, and `runner_daily_rate_max_bytes` now
publish their defaults (10 MiB, 64 MiB, and 2 GiB) in the config schema. The values in force are unchanged.
