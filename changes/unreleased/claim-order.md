---
kind: changed
surfaces: [http-api]
---

The runner peeks, `GET` and `POST /api/fleet/queue/peek`, now offer ready chunks in claim order: grouped by urgency,
most urgent first, and in rank order within a group. Runners of every release reach urgent work first, and no runner
needs an upgrade. `GET /api/queue` and the board's lists stay in rank order.
