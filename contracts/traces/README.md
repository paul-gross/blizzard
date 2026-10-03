# Trace contract corpus

The span shape the hub assembles for a step and a runner assembles for a lease, pinned so that a change to it is
deliberate.

- `dictionary.json` — the authored contract: every span name and its role, every event name, every attribute with its
  OTLP type, the roles that carry it and its meaning, the link reasons, the resource attributes, each role's scope, the
  scope and schema versions, and the id derivation with worked vectors. Edited by hand.
- `golden/<scenario>.json` — the span records the hub or a runner assembles from each seeded scenario in
  `tests/test_trace_contract.py`, with ids as lowercase hex, instants as ISO-8601 UTC and keys sorted. Never edited by
  hand.
- `golden/platform/<scenario>.json` — the OTLP/JSON body a worker command's span encodes to, seeded with a fixed clock
  and span id. Kept in its own directory because the fleet golden above holds assembled fleet spans. Never edited by
  hand.

`blizzard:trace-contract` (`uv run pytest tests/test_trace_contract.py`) compares the live assembly to the golden and
binds the dictionary to the names declared in code, to the golden's shape and to the published page.

Regenerate the golden after an intended change:

```bash
BLIZZARD_REGEN_TRACE_CONTRACT=1 uv run pytest tests/test_trace_contract.py
```

Regeneration rewrites the golden only. The dictionary is a separate, deliberate edit, and
[`docs/versioning.md`](../../docs/versioning.md#the-trace-contract) owns the policy for what may change and how.
