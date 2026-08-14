# SSE frame contract

The golden corpus for both daemons' `GET /api/events/stream` frame shapes (blizzard#317
Phase 2 broadened this from the hub-only scope it started as). Two scopes, each self-
contained: the **hub** scope at this directory's top level — `manifest.json` lists the
eight frame kinds the hub emits (`blizzard.hub.events.broker`) plus the framing
constants — and the **runner** scope at `runner/`, structured identically: its own
`manifest.json` lists the six frame kinds the runner emits
(`blizzard.runner.events.broker`) plus its own framing constants, `reserved_comment`
included — each daemon's stream opens with its own name. In both scopes, each
`<kind>.json` holds the named cases for that kind — the exact JSON payload object the
owning daemon emits for that call.

Both the Python producer-side contract test (`tests/test_sse_contract.py`) and the
TypeScript consumer-side contract spec (`web/projects/fleet/src/lib/sse/sse-contract.spec.ts`)
read these same files directly, over both scopes — there is no per-side copy. Moving a
golden reddens whichever side has not caught up to the change; changing a side's shape
without moving the golden reddens that side.

## Forward-compatibility policy

These are decisions, not accidents:

- **A consumer ignores unknown fields.** A payload field neither side's golden declares
  is not itself a break.
- **A consumer ignores unknown `event:` kinds**, proven at the transport level
  (`SseService`) for both scopes. The hub scope's own registered consumer, the board's
  `FleetLiveUpdates` frame dispatch, additionally returns `[]` for an unregistered event
  type rather than erroring; the runner scope's own consumer lands in `local-panel`
  (blizzard#317 Phase 4).
- **Absent is not the same as null, and both are load-bearing.** An optional field is
  omitted from the payload when it does not apply; the hub's `event-logged` and the
  runner's `fact-changed` each carry a `chunk_id` that is deliberately emitted as a
  present `null` (a runner-scoped hub event, or a runner-wide fact with no chunk) rather
  than omitted. A golden's use of one or the other is exact and both sides must honor it.
- **Comment frames carry no `data:` line and are never surfaced as events** — the
  reserved open-of-stream comment and the periodic keepalive comment, both named in
  `manifest.json`.
- **`id` is monotonic and is the reconnect cursor** (`Last-Event-ID`); it is not part of
  any golden payload, since it is assigned per-broadcast rather than per-kind.
