---
kind: fixed
surfaces: [http-api, wire, board]
---

A bounced chunk on the Delivery tab now shows its one bounce with its reason, not a second bounce with "No bounce
recorded on the chunk". Bounce reasons there read without code ticks, as the chunk dock shows them. An empty tenant
shows "Nothing delivering or delivered." in place of a bare divider, and a newly active flow no longer returns the list
to its loading skeleton.

`GET /api/chunks` rows carry `graph_name`, `graph_progress` (each node of the chunk's graph with its state: `current`,
`passed`, `failed` or `unvisited`; empty for a terminal chunk or a graph the hub cannot resolve), `latest_bounce` and
`bounce_count`. Each `usage` entry of a chunk's detail carries `runner_id`, the runner that ran the invocation, or
`null` when none was recorded. Flow record entries carry `verdict`, which is `landed` or `bounced` for a `turn-ended`
entry whose answer closed its submission and `null` otherwise, a fenced turn included; a flow's `last_verdict` follows
the same rule. They also carry `wait`, true only on a `turn-ended` entry whose turn waited (`pending` or
`awaiting-merge`), and `rework`, true only on one whose answer closed the submission and sent it back as routine rework
rather than a failure (today a `conflict`); a fenced conflict turn is not rework. A bounce, on `latest_bounce` and in a
chunk's `bounces`, carries `withdrawn`, whether its turn routed the chunk back and withdrew the submission from its
flows — a `poll-timeout` bounce included — and `rework`, true only for a `conflict` bounce whose turn routed it back;
one past the bounce cap escalates and is not rework. The board draws each chunk's graph progress and graph name from
these fields, so it no longer fetches each chunk's detail or each graph.
