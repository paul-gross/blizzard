# garden/finding-format

The shape a garden routine's run publishes for findings. This is blizzard's own format,
not a graph's — a garden graph's `artifacts:` map must never carry its own copy of it. The
delivery script validates a submission against exactly this shape, so read it live rather
than trusting a graph's baked-in memory of it: a format that changed under a run changed
the validator with it, and the worker that read the newer copy of this document is the one
whose delivery passes.

A survey step's own artifact is a list of candidates. A delivery step's own artifact is a
delta against the routine's live findings — related to a candidate but not the same shape,
because a delta acts on findings that may already exist.

## The candidate a survey emits

`ref` names a candidate only within its own submission — a later node in the same run can
refer to it before the hub ever mints a real id.

- `ref` — a local reference, stable only within this submission.
- `class` — the deployment's own vocabulary for a kind of weed. blizzard indexes it and
  never interprets it: it can count how often a class recurs without knowing what the name
  means. Keep the spelling stable across runs — two spellings of one class split its own
  recurrence count and hide the case for automating a fix.
- `locus` — where it lives: normally a repo-relative path, optionally `:line` or
  `::symbol`. A finding about a whole body of ground rather than one point inside it may
  name that ground instead. blizzard stores the string and never resolves it.
- `summary` — what was observed, in enough words for a person or a later pass to judge
  without re-deriving it.
- `introduced` — best effort: the commit that introduced what the finding objects to, from
  `blame` on the locus. Omit it rather than guess — a reformat defeats blame, and a rule
  that went stale because the standard around it moved has no introducing commit at all.

A finding is one instance — one thing somebody could fix — never a theme and never a
tally: seventeen instances in one package are seventeen candidates, each with its own
locus.

### FindingCandidate

```json
{
  "ref": "F1",
  "class": "stale-docstring",
  "locus": "src/billing/invoice.py:42",
  "summary": "Module docstring narrates the change history rather than the contract.",
  "introduced": "a1b2c3d"
}
```

## The delta a run delivers

A run's delivered artifact is not the routine's new standing state — it is the set of
changes to apply to it. Every delivered list declares the scope it was swept under, the
revision the run read per repository, and the measurement the routine's strategy asks
every run to record — properties of the artifact as a whole, present whether or not the
delta holds a single finding.

- `scope` — the scope this run swept.
- `revisions` — the revision read per repository, keyed by repo name; the baseline a
  later delta run against this same routine-and-scope pair is handed.
- `measurement` — the datapoint the routine's strategy asks every run to record; a clean
  run's measurement is its product even when `findings` is empty.
- `findings` — the operations below. Emit one only for a finding the run actually
  visited: a finding the run did not look at gets no entry, and keeps its last word.
  Silence about a finding is never a claim about it.

### FindingDelta

```json
{
  "scope": "test-files",
  "revisions": { "blizzard": "a1b2c3d" },
  "measurement": "312 files swept, 4 flagged",
  "findings": [
    {
      "op": "add",
      "class": "stale-docstring",
      "locus": "src/billing/invoice.py:42",
      "summary": "Module docstring narrates the change history rather than the contract.",
      "introduced": "a1b2c3d"
    },
    { "op": "observed", "id": "fin_01JKQ8Z3M4N5P6R7S8T9V0W1X2" },
    {
      "op": "gone",
      "id": "fin_01JKQ8Z3M4N5P6R7S8T9V0W1X3",
      "note": "No longer reproduces at the recorded locus."
    }
  ]
}
```

A candidate carried through unchanged, minus its `ref` — the hub mints an id for each
addition.

### AddFindingOp

```json
{
  "op": "add",
  "class": "stale-docstring",
  "locus": "src/billing/invoice.py:42",
  "summary": "Module docstring narrates the change history rather than the contract.",
  "introduced": "a1b2c3d"
}
```

The finding named by `id` still reproduces. No payload beyond the id: the finding was true
when it was recorded and it is true now, so there is nothing to revise — this op only
restamps when the finding was last seen and against which revision.

### ObservedFindingOp

```json
{ "op": "observed", "id": "fin_01JKQ8Z3M4N5P6R7S8T9V0W1X2" }
```

The run looked and could not find the finding named by `id`. This does not close the
finding — it flags it for a person, because a finding leaves the live set on human
judgment and never on a pass's word alone. `note` says why the run believes it is gone.

### GoneFindingOp

```json
{
  "op": "gone",
  "id": "fin_01JKQ8Z3M4N5P6R7S8T9V0W1X3",
  "note": "No longer reproduces at the recorded locus."
}
```
