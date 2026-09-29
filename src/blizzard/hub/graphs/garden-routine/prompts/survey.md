# Survey

You are running one pass of a garden routine. Look and record what you see — do not fix anything or decide what to do
about it.

## Your charge

The chunk's work item carries the routine, scope, and mode. The routine's name is the axis: the target's agent-context
entry point routes to the gardening-axes registry that declares every axis it tends — go only as far as that route
names, never hunting for something registry-shaped. The entry names what to look for (Evaluates), what you cover
(Scope), the standard it judges against (Criteria — follow it, not the restatement), and what to record every run
(Measurement).

No route to a registry, and a route to one that does not declare your axis, are the same gap: stop before sweeping.
Record one finding, class `undeclared-axis`, locus `gardening-axes registry`, summary the axis name — your whole output;
choice `no-strategy`.

## Scope discipline

Sweep the scope you were given, nothing outside it. The scope is a name, not a path: the registry entry says what it
covers. In delta mode — only what changed since this routine last ran, the baseline revisions your charge names — ground
outside it is not yours: a finding outside scope corrupts scoped runs' one guarantee.

## Gut-check before you enumerate

Before recording anything, sample the scope, then ask: **could you inventory this well within the context you have** —
not whether tedious, but whether you could finish and stand behind the list.

If the answer is no, fan out before you give up: partition the scope's files (by directory) into at most 10 batches and
hand each to a subagent (your harness's Agent or task tool) with its batch, the axis's Criteria, the scope discipline
above, and the candidate shape below; ask for candidates only. Merge results, normalize `class` spellings, dedupe, and
publish as usual.

Only if even 10 batches could not honestly cover the scope, stop and record a single finding, class `excessive-scope`,
with the scope itself as its locus and an honest count or estimate as its summary — that finding is your whole output,
and your choice is `excessive`. A truncated list posing as an inventory is worse than none: every later run inherits the
lie.

Where the criteria route to a command whose report lists the candidates, judge that report's size, not the scope's. Run
a command that outlasts one tool call in the background, polled in short calls.

## What to record

Record instances, not themes. One finding is one fixable thing at one locus: seventeen instances in one package are
seventeen entries — grouping is the docket's job.

A candidate is `ref` (stable within this submission only), `class`, `locus`, `summary`, and `introduced` (best effort,
omit rather than guess). Spell `class` as a stable, reusable kind of thing, not a one-off phrase for this instance — one
that reads the same run after run is what propose recognizes as ready to mechanize. Read the full shape:
`blizzard runner artifact get --scope system garden/finding-format --content`; on failure or an empty read, use the
restatement above.

Record the measurement your axis's registry entry declares whether or not you found anything — it is this run's product
even with no findings. Without a standard cited and a place it violates, you have an impression, not a finding; leave it
out.

Publish two assets, content on stdin: `blizzard runner artifact create --name survey`, a JSON object carrying `scope`,
`revisions` (per repository), `measurement`, and `candidates`, since reconcile enters cold; then
`blizzard runner artifact create --name delta`, the finding-delta shape from the same format document, same `scope`,
`revisions`, `measurement`, empty `findings`.

## A clean sweep still delivers

If you observed nothing worth recording, your choice is `clean` and the run goes straight to delivery: your empty
`delta` is delivered, its datapoint the run's product. On every other path reconcile assembles the real delta over
yours.
