# Resolve — judgement

Render your verdict on the delivery blocker. Your report rides forward as the `resolve-report` asset — if you have not yet run `blizzard runner attach --name resolve-report` with the diagnosis (per repo), what you changed, and whether any conflict hunk required a semantic choice, do that now before you record this verdict.

Select `resolved` only when the change's semantics are untouched: every conflict was mechanical (or there was nothing to fix — a transient forge state or an infra crash). This routes straight back to deliver, skipping re-verification — that is the point of this node, and it is only honest if no behavioral decision was made here.

Select `substantive` when resolving required a semantic choice — both sides edited the same behavior and you decided what the combined code does. The merge is pushed, but the change must re-earn its verification before delivering again.

Select `broken` when the PR never went clean because CI exposed a real defect in the change itself. Your `resolve-report` findings are carried into the build node's envelope so the next build attempt can address each one.

Alongside your verdict, submit this node's **retrospective** as its `retrospective` asset: run `blizzard runner attach --name retrospective` with a few honest lines on stdin — what went well, what didn't, and what the next node (or the next run) should know. The terminal retrospective node synthesizes these.
