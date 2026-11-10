---
kind: added
surfaces: [http-api, cli]
---

The hub explains, for one runner, the chunk it reaches for next and every chunk it passes on the way there, each with
its urgency and why it was passed: blocked, naming the first prerequisite and how many there are, or ineligible.
`GET /api/runners/{runner_id}/next-claim` returns the explanation, and `blizzard hub runner next RUNNER` prints it.
