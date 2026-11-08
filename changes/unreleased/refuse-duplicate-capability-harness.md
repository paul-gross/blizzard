---
kind: fixed
surfaces: [http-api, wire]
---

A runner registration whose capabilities name the same `harness_id` more than once is now refused with `422`
(`urn:blizzard:problem:duplicate-capability-harness`) before anything is recorded, instead of storing the repeats.
