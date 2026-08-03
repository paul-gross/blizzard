# SSE frame contract

The golden corpus for the hub's `GET /api/events/stream` frame shapes. `manifest.json`
lists the eight frame kinds the hub emits (`blizzard.hub.events.broker`) plus the
framing constants; each `<kind>.json` holds the named cases for that kind — the exact
JSON payload object the hub emits for that call.

Both the Python producer-side contract test (`tests/test_sse_contract.py`) and the
TypeScript consumer-side contract spec (`web/projects/fleet/src/lib/sse/sse-contract.spec.ts`)
read these same files directly — there is no per-side copy. Moving a golden reddens
whichever side has not caught up to the change; changing a side's shape without moving
the golden reddens that side.

## Forward-compatibility policy

These are decisions, not accidents:

- **A consumer ignores unknown fields.** A payload field neither side's golden declares
  is not itself a break.
- **A consumer ignores unknown `event:` kinds.** The board's frame dispatch returns `[]`
  for an unregistered event type rather than erroring.
- **Absent is not the same as null, and both are load-bearing.** An optional field is
  omitted from the payload when it does not apply; `event-logged`'s `chunk_id` is the one
  field on this wire that is deliberately emitted as a present `null` (a runner-scoped
  event has no chunk) rather than omitted. A golden's use of one or the other is exact
  and both sides must honor it.
- **Comment frames carry no `data:` line and are never surfaced as events** — the
  reserved open-of-stream comment and the periodic keepalive comment, both named in
  `manifest.json`.
- **`id` is monotonic and is the reconnect cursor** (`Last-Event-ID`); it is not part of
  any golden payload, since it is assigned per-broadcast rather than per-kind.
