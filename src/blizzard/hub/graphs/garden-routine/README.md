# The packaged garden-routine graph

The packaged copy of the prebaked plan artifact at `blizzard-product:/plans/garden/artifacts/garden-routine/`. The plan
froze when this shipped; a correction the graph needs to mint or to run lands here and is recorded below, never in the
plan.

Conventions for the `prompts/` tree are owned by the
[prompt-authoring README](../advanced-development-workflow/README.md) — read it before adding or editing a node prompt.

## Corrections over the plan artifact

- **The `survey` session pool is named `sweep`.** Mint validation refuses a pool sharing a node's name — `resume:survey`
  would resolve to the session and never the node — and the plan named both the pool and the entry node `survey`. The
  policy is unchanged: `survey` on `fresh:sweep`, `reconcile` on `fresh:match`, `propose` on `resume:match`.
- **`deliver` names its artifacts.** The plan invoked `garden_deliver` bare; the shipped script takes the delivering
  artifact names, so the packaged node runs it with `--delta delta --proposals docket`.
- **`survey` also produces `delta`.** The `clean` choice routes `survey → deliver` with no `reconcile` in between, and
  delivery validates an artifact named `delta` — but a completion publishes only the names its node declares, so
  `survey` declares `delta` and every sweep publishes the empty skeleton carrying the run's scope, revisions, and
  measurement. Delivery reads the newest `delta`, so reconcile's assembled one supersedes the skeleton on every path
  where reconcile runs.
- **The prompts name their verbs.** The plan's prompts pointed at "the `findings` doc" and "the CLI" abstractly, from
  before the verbs existed; the packaged prompts name the runtime reads
  (`blizzard runner artifact get --scope system
  garden/finding-format`, `blizzard runner garden findings`) and the
  writes (`blizzard runner artifact create`). The `survey` asset is specified as an envelope — scope, revisions,
  measurement, candidates — because `reconcile` enters on a cold session and the delta it delivers needs all three
  run-level facts.
- **`survey.md` is condensed** to the packaged 4,000-byte node-prompt bar (`tests/test_prompt_byte_bars.py`); every rule
  survives, some rationale does not.
- **`deliver` authors a `failure` edge.** The plan left `garden_deliver`'s own nonzero-exit paths (a missing env var, a
  failed POST, an unrecognized response, a failed marker write) with no edge to route through, which deadlocks the chunk
  per `bzh:hub-node-outcome-protocol`. Routes to `propose`, which re-affirms the standing docket and retries; bounded by
  a loop-bound check in its addendum (`propose.from-deliver-failure.md`) the same way the `invalid` bounce is bounded in
  `reconcile.from-deliver.md`.
- **Every system-scope pointer carries the additive fallback `bzh:graph-artifact-pointer-fallback` requires** —
  `survey.md`, `reconcile.md`, `propose.md`, and `reconcile.from-deliver.md` each restate the minimum shape they point
  at, so a failed or empty read still carries the node-step to completion.
- **`propose.md` lets a proposal cite its own run's `add`.** A proposal's `findings` entry names either a `fin_` id
  already live on this routine or the submission-local `ref` an `add` op in this run's own delta carries, resolved
  against the id the hub mints for it at delivery — so a first-run `excessive-scope` or `undeclared-axis` candidate is
  citable by that same run's hand-out proposal.
- **`reconcile.from-deliver.md` named a `findings` doc the packaged graph never ships.** Corrected to the same runtime
  read the sibling prompts use, and given the `invalid` bounce's own loop-bound check.
- **The plan's "strategy" is the target's own gardening-axes registry, not a field the charge carries.** The plan
  artifact had `survey.md` read "the strategy" straight out of the chunk's work item and stop-and-escalate when a named
  standard did not exist; `canon:gardening-axes` already owns this ground, so `survey.md` instead resolves the routine's
  own name as an axis from the registry the target project declares, follows that entry's Criteria pointer to the
  standard's actual home, and records its declared Measurement — naming the registry by concept only, never by path, so
  the one packaged graph still drives a non-blizzard deployment unchanged.
- **`survey` gained a `no-strategy` choice, mirroring `excessive` exactly.** An axis the target's registry does not
  declare is a finding about the harness, not a reason to improvise or to escalate: one candidate, class
  `undeclared-axis`, locus `gardening-axes registry`, no inventory, routed to `reconcile` the same way `excessive` is. A
  machinery-level class the packaged prompts declare, the same standing as `excessive-scope`.
- **The plan's "strategy" vocabulary for `class` is retired the same way `survey`'s is.** The plan artifact had
  `propose.md` name `class` as "one of the response classes your routine's strategy declares"; canon's four required
  registry fields carry no response-class vocabulary, so `class` is the deployment's own taxonomy for a kind of response
  — the hub stores, indexes, and groups by it, and never interprets it.
