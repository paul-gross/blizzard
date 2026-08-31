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
  before the verbs existed; the packaged prompts name the runtime reads (`blizzard runner artifact get --scope system
  garden/finding-format`, `blizzard hub finding list`) and the writes (`blizzard runner artifact create`). The `survey`
  asset is specified as an envelope — scope, revisions, measurement, candidates — because `reconcile` enters on a cold
  session and the delta it delivers needs all three run-level facts.
- **`survey.md` is condensed** to the packaged 4,000-byte node-prompt bar (`tests/test_prompt_byte_bars.py`); every
  rule survives, some rationale does not.
