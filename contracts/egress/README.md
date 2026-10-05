# Egress contract corpus

The shape of the `steps`, `invocations` and `events` datasets the hub exports to a directory, pinned so that a change
to it is deliberate.

- `dictionary.json` — the authored contract: per dataset its major version, identity columns, partition column, the
  views it publishes, and each column's name, type, nullability, meaning and enumerated values (`closed` or open). The
  `events` columns also record the record types each appears on. Edited by hand.
- `<view>.sql` — each published view, over a relation named after its dataset: `steps_newest.sql`,
  `invocations_newest.sql`, and for `events` the current-truth view `events_current.sql` and the per-extractor-version
  view `events_by_version.sql`. Both events views also collapse repeated copies of an event. Edited by hand.
- `_schema/<dataset>.v<major>.json` — the schema document the writer places in an export, rendered from the dictionary
  by the writer's own serializer. Never edited by hand.
- `golden/<scenario>/` — the decompressed NDJSON files and manifests, without digests, that the real writer produces for
  the seeded scenario in `tests/test_egress_contract.py`. Never edited by hand.

`blizzard:egress-contract` (`uv run pytest tests/test_egress_contract.py`) binds the code's schemas to the dictionary,
compares the writer's output in both formats to the golden, checks `_schema/` and the generated dictionary block of
[`docs/deployment/egress.md`](../../docs/deployment/egress.md) for staleness, and runs every
published view in DuckDB, the events views over the golden's per-segment cases.

Regenerate after an intended change:

```bash
BLIZZARD_REGEN_EGRESS_CONTRACT=1 uv run pytest tests/test_egress_contract.py
```

Regeneration rewrites `golden/`, `_schema/` and the dictionary block only. The dictionary and the views are separate,
deliberate edits, and [`docs/versioning.md`](../../docs/versioning.md#the-egress-contract) owns the policy for what may
change and how.
