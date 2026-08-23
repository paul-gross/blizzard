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
