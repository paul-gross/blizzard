# Retrospective — this round's deferred review findings were lost

`record-findings` could not materialize the review round's `review-finding-delta` — either the artifact failed shape
validation, or the delivery script itself failed. The landing above it is unaffected: the code merged. What did not
happen is that this pass's deferred (`should-fix`) review findings never became durable `fin_` rows — no later garden
sweep or `hub finding list --source review` read will ever see them.

Record this under **What Didn't Go Well**: that the deferred findings were lost, and — if you can still see the
`review-finding-delta` or `review-findings` asset from this run's history — which findings they were, so a person can
decide whether to re-file any of them by hand. Do not attempt to re-submit the delta yourself; this node does not
re-run, and the chunk has already landed.
