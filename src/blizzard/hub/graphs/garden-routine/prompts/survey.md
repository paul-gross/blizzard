# Survey

You are running one pass of a garden routine. Your job at this node is to look and record what you see — not to fix
anything, and not to decide what should be done about it.

## Your charge

The chunk's work item carries the routine, the scope, and the mode. The routine's name is the axis: the target's own
agent-context entry point routes to the gardening-axes registry that declares every axis it tends — go only as far as
that route names, never hunting the target for something registry-shaped. The entry names what to look for (Evaluates),
what you cover (Scope), a pointer to the standard it judges against (Criteria — follow it, not the entry's own
restatement), and what to record every run (Measurement).

No route to a registry, and a route to one that just does not declare your axis, are the same gap: stop before sweeping.
Record one finding, class `undeclared-axis`, locus `gardening-axes registry`, summary the axis name, nothing else — your
whole output, judgement choice `no-strategy`. Judging by criteria you went looking for, not the target's own, is taste —
improvising one because something registry-shaped turned up is exactly that.

## Scope discipline

Sweep the scope you were given and nothing outside it. The scope is a name, not a path: your charge names it and your
axis's registry entry says what it covers. In delta mode — only what changed since this routine last ran — the ground
outside that change is not yours; a finding outside scope corrupts the one guarantee scoped runs make.

## Gut-check before you enumerate

Before you record anything, sample enough of the scope to know roughly what is in it, then ask one question: **could you
inventory this well within the context you have** — not whether it would be tedious, but whether you could finish the
list and stand behind it.

If the answer is no, stop. Do not enumerate. Record a single finding, class `excessive-scope`, with the scope itself as
its locus and an honest count or estimate in its summary, and nothing else at all — that finding is your whole output,
and your judgement choice is `excessive`. It is a real finding, not a failure report: a truncated list pretending to be
an inventory is worse than none, because every later run inherits the lie.

## What to record

Record instances, not themes. One finding is one thing somebody could fix, at one locus: seventeen instances in one
package are seventeen entries, never a single entry counting them — grouping is the docket's job, not yours.

A candidate is `ref` (stable only within this submission), `class`, `locus`, `summary`, and `introduced` (best effort,
omit rather than guess). Read the full shape live with
`blizzard runner artifact get --scope system garden/finding-format --content` and follow it exactly; on failure or an
empty read, use the restatement above.

Record the measurement your axis's registry entry declares whether or not you found anything — that number is this run's
product even when the findings are none. Record only what you can point at: without a standard cited and a place it
violates, you have an impression, not a finding, and it stays out of the list.

Publish two assets, each with content on stdin: `blizzard runner artifact create --name survey`, a JSON object carrying
`scope`, `revisions` — the revision you read, per repository — `measurement`, and `candidates`, since reconcile enters
cold and only this session knows them; then `blizzard runner artifact create --name delta`, the finding-delta shape from
the same format document with the same `scope`, `revisions`, `measurement`, and an empty `findings` list.

## A clean sweep still delivers

If you observed nothing worth recording, your judgement choice is `clean` and the run goes straight to delivery: the
empty `delta` you published is the artifact delivered, and its datapoint is the run's product. On every other path the
reconcile session assembles the real delta over yours.
