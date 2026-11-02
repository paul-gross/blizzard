---
kind: fixed
surfaces: [http-api, wire, board]
---

A bounced chunk on the Delivery tab now shows its one bounce with its reason, not a second bounce with "No bounce
recorded on the chunk". Bounce reasons there read without code ticks, as the chunk dock shows them. An empty tenant
shows "Nothing delivering or delivered." in place of a bare divider, and a newly active flow no longer returns the list
to its loading skeleton.

`GET /api/chunks` rows carry `graph_name`, `graph_progress` (each node of the chunk's graph with its state: `current`,
`passed`, `failed` or `unvisited`), `latest_bounce` and `bounce_count`. Flow record entries carry `verdict`, which is
`landed` or `bounced` for a `turn-ended` entry that judged its submission and `null` otherwise. The board draws each
chunk's graph progress and graph name from these fields, so it no longer fetches each chunk's detail or each graph.
