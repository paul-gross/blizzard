---
kind: changed
surfaces: [http-api, wire, cli, board]
---

Every client blizzard ships — the runner, its hub client, the `blizzard` CLI, the board, and the runner panel — now
tolerates a response enum value it does not know: it shows the raw value, skips it, or defers to the server's own state,
and never fails the read. The OpenAPI documents mark each position whose clients tolerate it with `x-blizzard-open`, and
a response enum gaining a value is additive against a release whose clients read that position open — `0.3.0` onward.
Against an older supported release it still breaks unless an allowance admits it;
[`docs/versioning.md`](../../docs/versioning.md) owns the rule. Servers still refuse an unknown value in a request, and
a field a runner dispatches work on stays closed. A client of the HTTP API must tolerate an unknown value in a response
enum the same way.
