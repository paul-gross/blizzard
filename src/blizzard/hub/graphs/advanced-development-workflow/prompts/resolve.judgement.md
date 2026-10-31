# Resolve — judgement

Render your verdict on the delivery blocker. The `resolve-report` asset must be published before it — the diagnosis per
repo, what you changed, and whether any conflict hunk required a semantic choice.

Select `resolved` only when the change's semantics are untouched: every conflict was mechanical, or there was nothing to
fix at all — and only once every repo's PR reads mergeable and no check on the head sha is red. A red check never clears
on its own, so a check you judge flaky is never `resolved` with no change. This routes straight back to deliver,
skipping re-verification — only honest if no behavioral decision was made here. Entering from `retrospective`, the same
standard applies: select `resolved` once every repo needing a merge is genuinely merged.

Select `substantive` when resolving required a semantic choice: the merge is pushed, but the change re-earns its
verification before delivering again.

Select `broken` when the PR never went clean because CI exposed a defect — in the change itself, or a flaky test or
harness the re-run cleared; your `resolve-report` findings ride back into the build node.

Alongside your verdict, submit this node's retrospective: run `blizzard runner artifact create --name retrospective`
with a few honest lines on stdin — what went well, what didn't, and what the next node or run should know.
