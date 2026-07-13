# Review — judgement

Render your review verdict on the submitted work. Your assessment payload is the
`review-findings` asset the chunk carries forward, so state what you reviewed, the
results of the checks and e2e flows you ran, and every blocking issue you found.

Select `pass` if the work meets the PM item's intent, the checks and flows are green,
and you found no blocking issue — the chunk proceeds to delivery. Select `fail` if
any blocking issue remains; your findings are carried back into the build node's
envelope, so the next build attempt can address each one.
