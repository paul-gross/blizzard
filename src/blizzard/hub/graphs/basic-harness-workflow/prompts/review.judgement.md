# Review — judgement

Before recording the verdict, run `blizzard runner artifact create --name review-findings` with the findings on stdin,
unless this attempt already has. The `review-findings` asset must record how every entry in `review-finding-refutes` was
adjudicated, accepted and rejected alike.

Record `pass` when the work meets the work item's intent, is well-formed against the review axes applied, and no
blocking issue remains; the chunk then goes to the `pre-push` node. Record `fail` when any blocking issue remains — the
`review-findings` asset rides back into the `iterate` node's envelope.

## The review-finding-delta asset

Before your verdict, on every round, also run `blizzard runner artifact create --name review-finding-delta`, content
on stdin: a JSON object `{"entries": [...]}`, one entry per finding you have adjudicated this round — `fixed` or
`refuted`, plus, only on a `pass`, `deferred` for a still-open should-fix. Leave out a blocking finding still open on a
`fail`: it is why the round failed, not something this delta records. Each entry carries `ref` and `disposition`
(`deferred`, `fixed`, or `refuted`). A `deferred` entry additionally carries `severity`, `scope` — run
`blizzard runner scope list` first and reuse a listed slug where one already fits, rather than inventing one — `class`,
`locus`, and `summary`; `fixed`/`refuted` carry no more than `ref` and `disposition`. A `deferred` entry can never
carry `severity: blocking` — that cannot coexist with `pass`. Read the full shape live with `blizzard runner artifact
get --scope system review/finding-format --content` and follow it exactly; on failure or an empty read, use the
restatement above.
