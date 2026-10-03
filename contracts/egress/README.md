# Egress contract corpus

The shape of the `steps` and `invocations` datasets the hub exports to a directory, pinned so that a change to it is
deliberate.

- `dictionary.json` — the authored contract: per dataset its major version, identity column, partition column, and each
  column's name, type, nullability, meaning and enumerated values (`closed` or open). Edited by hand.
- `steps_newest.sql`, `invocations_newest.sql` — the authored newest-copy view of each dataset, over a relation named
  after the dataset. Edited by hand.
- `_schema/<dataset>.v<major>.json` — the schema document the writer places in an export, rendered from the dictionary
  by the writer's own serializer. Never edited by hand.
- `golden/<scenario>/` — the decompressed NDJSON files and manifests, without digests, that the real writer produces for
  the seeded scenario in `tests/test_egress_contract.py`. Never edited by hand.

`blizzard:egress-contract` (`uv run pytest tests/test_egress_contract.py`) binds the code's schemas to the dictionary,
compares the writer's output in both formats to the golden, checks `_schema/` and the generated dictionary block of
[`docs/deployment/egress.md`](../../docs/deployment/egress.md) for staleness, and runs the published newest-copy views
in DuckDB.

Regenerate after an intended change:

```bash
BLIZZARD_REGEN_EGRESS_CONTRACT=1 uv run pytest tests/test_egress_contract.py
```

Regeneration rewrites `golden/`, `_schema/` and the dictionary block only. The dictionary and the views are separate,
deliberate edits, and [`docs/versioning.md`](../../docs/versioning.md#the-egress-contract) owns the policy for what may
change and how.
