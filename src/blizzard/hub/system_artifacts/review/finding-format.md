# review/finding-format

The shape a delivery lane's review round publishes for its unfixed findings (blizzard#582). This is blizzard's own
format, not a graph's — a lane graph's `artifacts:` map must never carry its own copy of it. The `record-findings` node
validates a submission against exactly this shape, so read it live rather than trusting a graph's baked-in memory of
it.

A review round's own delta is a flat list of entries, one per finding the review adjudicated — never a bucket of
changes against prior state the way a garden run's delta is: a review has no prior round to diff against, only the
findings it raised or answered this pass.

## The entry a review emits

Every entry carries the review's own local `ref` (`F1`, `F2`, …) and what became of that finding this round.

- `ref` — the review's own local reference for this finding, matching the anchor the `review-findings` asset already
  cites it by.
- `disposition` — `"deferred"`, `"fixed"`, or `"refuted"`. `deferred` is the only disposition that mints: the finding
  is real and stands unanswered. `fixed` and `refuted` are a record of what the review adjudicated — the review already
  settled them, so materialization mints nothing further for either.

A `deferred` entry additionally carries the four `garden/finding-format` `AddFindingOp` fields, in the same meanings
that document declares them (read it for the full field-by-field rationale — this restates only the two review adds):

- `severity` — `"blocking"` or `"should-fix"`. A `deferred` entry can never carry `"blocking"`: a passing review
  cannot hold a blocking finding by definition, so one on the wire is rejected outright, not silently downgraded.
- `scope` — the scope this finding is filed under. Named freely; an unfamiliar slug is minted rather than rejected
  (blizzard#582 D2), and a retired one is still accepted — recording a finding is not running against the scope.
- `class` — the lane's own review-axis name (correctness, simplification, efficiency, …), the deployment's own
  vocabulary exactly as `garden/finding-format`'s `class` is.
- `locus` — where it lives, the same meaning and the same freedom to name a whole body of ground instead of one point.
- `summary` — what was observed, in enough words to judge without re-deriving it.

A `fixed` or `refuted` entry carries only `ref` and `disposition` — no `severity`, `scope`, `class`, `locus`, or
`summary`. Sending them anyway is not rejected, but they are read by nothing: the review's own record of *why* a
finding was fixed or refuted already lives in the `review-findings` asset and `review-finding-refutes`, not here.

### ReviewFindingEntry

```json
{
  "ref": "F1",
  "disposition": "deferred",
  "severity": "should-fix",
  "scope": "blizzard",
  "class": "simplification",
  "locus": "src/billing/invoice.py:42",
  "summary": "Three near-identical branches could fold to one with a lookup table."
}
```

### ReviewFindingDelta

```json
{
  "entries": [
    {
      "ref": "F1",
      "disposition": "deferred",
      "severity": "should-fix",
      "scope": "blizzard",
      "class": "simplification",
      "locus": "src/billing/invoice.py:42",
      "summary": "Three near-identical branches could fold to one with a lookup table."
    },
    { "ref": "F2", "disposition": "fixed" },
    { "ref": "F3", "disposition": "refuted" }
  ]
}
```

## What gets rejected

The whole delta is refused, writing nothing, on: malformed JSON or a shape mismatch against this format; a `ref`
carried by more than one entry; a `deferred` entry missing one of its five required fields; a `deferred` entry marked
`blocking`; or a `deferred` entry naming a malformed scope slug. There is no partial delivery — a delta that fails
validation mints none of its `deferred` entries, not even the ones that would have passed alone.

## What a review-sourced finding is not

It arrives in no finding set (`garden/finding-format`'s own `revisions`/`measurement` envelope has no counterpart
here): a review delta spans whatever scopes its entries name and has no run or routine behind it to measure. It carries
no `routine_name` and answers to no routine's own delta-diffing lineage — `observed`/`gone` operate on it exactly as on
any routine-sourced finding, but only from a run sweeping the same scope it was filed under.
