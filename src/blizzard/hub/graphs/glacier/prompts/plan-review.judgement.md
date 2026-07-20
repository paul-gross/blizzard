# Plan review — judgement

Render your gate verdict. Your findings ride forward as the `plan-findings` asset — if you have not yet run `blizzard runner attach --name plan-findings` with your findings on stdin, do that now before you record this verdict.

Select `pass` only if both gates hold with no must-fix finding — the chunk proceeds to build on this plan. Select `must-fix` if any blocking finding remains; your findings ride back into the plan node for revision.

Alongside your verdict, submit this node's **retrospective** as its `retrospective` asset: run `blizzard runner attach --name retrospective` with a few honest lines on stdin — what went well, what didn't, and what the next node (or the next run) should know. The terminal retrospective node synthesizes these.
