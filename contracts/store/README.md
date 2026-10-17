# Store contract documents

The schema each store's `schema.py` declares, pinned so that a change to it is deliberate.

- `hub.json`, `runner.json` — one document per store: its `head_revision` (the migration tree's latest revision) and, per
  table, each column's portable `type`, `nullable`, whether it carries a `server_default`, its primary key, unique
  constraints, foreign keys, the names of its check constraints, and a `deprecated` mark. Never edited by hand.

Server defaults are recorded by presence only, constraints by their columns and referents (checks by name), and indexes,
column order and `alembic_version` are left out: each is a rule a reflected store reproduces on any dialect.

A table or column is marked deprecated in `schema.py` with `info={"deprecated": "<MAJOR.MINOR>"}`; the document carries
the string verbatim, and `null` when unmarked.

`blizzard-export-contracts` builds the documents from `schema.py` alone, so the merge gate's `contract drift` step turns
red when `schema.py` changes without a regenerated document. `blizzard:store-contract`
(`uv run pytest tests/test_store_contract.py`) reflects a head-migrated store, on SQLite, and holds it to the document, so
it turns red when a revision and `schema.py` disagree.

Regenerate after an intended change:

```bash
mise run export-contracts
```
