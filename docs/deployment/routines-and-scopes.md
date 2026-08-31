# Routines and scopes

A **scope** is an operator-authored slug plus a description; a **routine** names the graph its runs execute, a default
scope, and model/effort run defaults. Both are hub-stored records — the hub indexes and hands back a scope slug, never
resolving what it names, and nothing here filters by scope; that is a separate surface.

## Scopes

`blizzard hub scope create <slug> [--description]`, `list`, `edit <slug> --description <text>`, `retire <slug>`, and
`enable <slug>` are the scope verbs. `create` is a mint-or-no-op: naming an existing slug leaves its stored description
untouched — `edit` is the only verb that changes it. `retire`/`enable` are the graph lifecycle's own reversible,
append-only brake: retiring a scope appends `scope.retired`, `enable` appends `scope.enabled`, and neither touches the
stored slug or description.

## Routines

`blizzard hub routine create <name> <graph_name> <default_scope_slug> [--model] [--effort]`, `list`, `show
<routine_id>`, and `edit <routine_id> --graph <name> --scope <slug> [--model] [--effort]` are the routine verbs.
`GRAPH_NAME` must resolve to a currently-enabled graph — a create or edit naming one that does not refuses, naming it.
`DEFAULT_SCOPE_SLUG` is minted through the same path `scope create` uses if the slug is unseen, so a routine's default
scope never needs a separate `scope create` first.

A routine's `name` is its lineage and is immutable once minted: `routine edit` never changes it, and a create naming an
already-existing routine name is refused rather than duplicating it. `routine_id` is the id every other verb addresses
the routine by; `edit` still requires the current name be restated, and refuses a request that names a different one.

## Running one

`blizzard hub routine run <name> [--scope <slug>] [--mode full|delta] [--note <text>]` mints, ingests, and promotes a
hub work item from the named routine, in one act. `NAME` resolves to the routine's `routine_id` through the routine
list; `--scope` overrides the routine's own default, minting an unseen slug the same way `scope create` does.
`--mode` defaults to `full`; a requested `delta` against a routine/scope pair with no recorded baseline downgrades to
`full` rather than refusing — the CLI names the downgrade in its output, and the item's own charge does too. A retired
effective scope, or a routine whose graph has lost every enabled mint, refuses the run rather than running it anyway.
