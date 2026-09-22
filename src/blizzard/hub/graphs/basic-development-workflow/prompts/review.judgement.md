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

Before your verdict, on every round, also run `blizzard runner artifact create --name review-finding-delta`, content on
stdin: one entry per finding named in your `review-findings` submission, `ref` and `disposition` (`deferred`, `fixed`,
or `refuted`). A `deferred` entry — a should-fix finding still unanswered on a `pass` — additionally carries
`severity`, `scope`, `class`, `locus`, and `summary`; `fixed`/`refuted` carry no more than `ref` and `disposition`. A
`deferred` entry can never carry `severity: blocking` — that cannot coexist with `pass`. Read the full shape live with
`blizzard runner artifact get --scope system review/finding-format --content` and follow it exactly; on failure or an
empty read, use the restatement above.
