# Review — judgement

You are closing a review node-step: record the review verdict.

Submit your findings as the `review-findings` asset with `blizzard runner artifact create --name review-findings`,
content on stdin, before you record the verdict. Record in that asset how you adjudicated every entry in
`review-finding-refutes`, accepted and rejected alike. A finding whose refutation you accepted is resolved, and does not
block `pass`.

| Outcome | Record it when                                                                                        |
| ------- | ----------------------------------------------------------------------------------------------------- |
| `pass`  | The work meets the work item's intent, the end-to-end flows are clean, and no blocking issue remains. |
| `fail`  | Any blocking issue remains.                                                                           |

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
