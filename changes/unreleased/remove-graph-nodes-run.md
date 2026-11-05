---
kind: removed
surfaces: [store]
retires: deprecate-graph-nodes-run
breaking: true
breaks:
  - graph_nodes.run
upgrade-guide: runners-below-03-are-refused
---

Drops the hub store's `graph_nodes.run` column, which the hub has neither read nor written since 0.3: a hub node is
taken by its `action`, and a command the carry-over could not translate is kept in `graph_nodes.legacy_run`.

## Upgrade

Run `blizzard hub migrate` as for any release. Rewrite any query of your own that reads `graph_nodes.run` onto
`graph_nodes.action` and `graph_nodes.legacy_run`. A `blizzard hub migrate --down` past this release re-adds the column
and fills each hub node's `run` from its `legacy_run` or its action.
