# Review — judgement

Render your review verdict on the submitted work. Your findings ride forward as the `review-findings` asset — if you
have not yet run `blizzard runner artifact create --name review-findings` with your findings on stdin, do that now,
before you record this verdict. That asset must record how you adjudicated every entry in `review-finding-refutes`,
accepted and rejected alike; a finding whose refutation you accepted is resolved and does not block `pass`.

Select `pass` if the work meets the work item's intent, is well-formed by the review axes you applied, and you found no
blocking issue — the chunk proceeds to delivery. Select `fail` if any blocking issue remains; the attached
`review-findings` asset is carried back into the build node's envelope, so the next build attempt can address each one.
