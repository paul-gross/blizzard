# Survey

You are running one pass of an ideation routine. Your job at this node is to look for gaps between the target as built
and the intent it was built to serve — not to fix anything, and not to decide what to do about it. You judge against
intent and enforce nothing: nothing you record is a rule the target broke.

## Your charge

The chunk's work item carries the routine and the scope. The routine's name is the axis: the target's own agent-context
entry point routes to the gardening-axes registry that declares every axis it tends — go only as far as that route
names, never hunting the target for something registry-shaped. The entry names what to look for (Evaluates), what you
cover (Scope), a pointer to the charter it judges against (Criteria — follow it, not the entry's own restatement), and
what to record every run (Measurement).

## When the axis is undeclared

No route to a registry, and a route to one that just does not declare your axis, are the same gap. Before treating it
as final: read `blizzard runner chunk history` for a prior `ask` on this same axis that is already answered — if one
exists, re-check the registry once; an answered ask ordinarily means someone has just updated it.

Still undeclared, and no prior ask on record: ask now, `blizzard runner ask` naming the axis and asking that its entry
be added (or that its absence be confirmed deliberate), and let the answer arrive before you continue.

Still undeclared after that: publish `survey` and `delta` empty — `scope`, `revisions: {}`, `measurement` blank or zero,
`candidates`/`findings` empty lists — and your judgement choice is `undeclared`. Nothing else about this pass runs.

## Always full

Sweep the scope you were given, whole, every run. This graph has no delta mode: an absence of a capability does not
shrink because nothing changed since the last pass, so there is no "since last time" to confine yourself to.

## What to record

Record instances, not themes: one finding is one gap a person could act on, at one locus — a persona, a workflow, or a
question the charter describes. A candidate is `ref` (stable only within this submission), `locus` (the persona,
surface, or workflow it concerns), and `summary` (what is missing and why it matters, in enough words that reconcile
and propose can judge it without re-deriving your reasoning). Point at something concrete: without a place in the
charter's own intent to cite, you have a preference, not a candidate, and it stays out of the list.

Record the measurement your axis's registry entry declares whether or not you found anything. Its real value is not
yet knowable this early — propose corrects it once it knows what it proposed — so record it as zero or blank here.

Publish two assets, each with content on stdin: `blizzard runner artifact create --name survey`, a JSON object carrying
`scope`, `revisions: {}`, `measurement`, and `candidates`, since reconcile enters cold and only this session knows them;
then `blizzard runner artifact create --name delta`, the finding-delta shape from
`blizzard runner artifact get --scope system garden/finding-format --content` with the same `scope`, `revisions: {}`,
`measurement`, and an empty `findings` list — this graph never adds, observes, or closes a finding.

## When nothing is missing

If you swept the whole scope and found nothing wanting, your judgement choice is `empty` and the run goes straight to
delivery: the skeleton `delta` you published is the artifact delivered, and its datapoint is the run's product.
