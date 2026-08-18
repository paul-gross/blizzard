# Resolve — judgement

Render your verdict on the delivery blocker. The `resolve-report` asset must be published before it — the diagnosis per
repo, what you changed, and whether any conflict hunk required a semantic choice.

Select `resolved` only when the change's semantics are untouched: every conflict was mechanical, or there was nothing to
fix at all. This routes straight back to deliver, skipping re-verification — only honest if no behavioral decision was
made here. Entering from `retrospective`, the same standard applies: select `resolved` once every repo needing a merge
is genuinely merged.

Select `substantive` when resolving required a semantic choice: the merge is pushed, but the change re-earns its
verification before delivering again.

Select `broken` when the PR never went clean because CI exposed a real defect in the change itself; your
`resolve-report` findings ride back into the build node.

Alongside your verdict, submit this node's retrospective: run `blizzard runner artifact create --name retrospective`
with a few honest lines on stdin — what went well, what didn't, and what the next node or run should know.
