# Review — judgement

Render your review verdict on the submitted work. Your findings ride forward as the
`review-findings` asset — if you have not yet run
`blizzard runner attach --name review-findings` with your findings on stdin, do that
now before you record this verdict.

Select `pass` if the work meets the PM item's intent, the checks and flows are green,
and you found no blocking issue — the chunk proceeds to the pre-push integration
step. Select `fail` if any blocking issue remains; the attached `review-findings`
asset is carried back into the build node's envelope, so the next build attempt can
address each one.
