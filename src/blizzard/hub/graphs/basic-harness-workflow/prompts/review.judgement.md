# Review — judgement

Before recording the verdict, run `blizzard runner artifact create --name review-findings` with the findings on stdin,
unless this attempt already has. The `review-findings` asset must record how every entry in `review-finding-refutes` was
adjudicated, accepted and rejected alike.

Record `pass` when the work meets the work item's intent, is well-formed against the review axes applied, and no
blocking issue remains; the chunk then goes to the `deliver` node. Record `fail` when any blocking issue remains — the
`review-findings` asset rides back into the `build` node's envelope.
