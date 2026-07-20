# Build — judgement

Assess the build you just completed: every phase of the plan is implemented in order, scoped as planned, with your work committed and pushed.

Select `pass` only if all hold — the work then hands to the verify node. Select `fail` if a phase is incomplete or the work is not committed and pushed — the failure output will be attached when the build node is re-entered.

Alongside your verdict, submit this node's **retrospective** as its `retrospective` asset: run `blizzard runner attach --name retrospective` with a few honest lines on stdin — what went well, what didn't, and what the next node (or the next run) should know. The terminal retrospective node synthesizes these.
