# Build — judgement

Assess the build you just completed. Every phase of the plan is implemented, in order and scoped as planned. Your work is committed, pushed, and declared — `blizzard runner artifact commit` for each repo you touched.

Judge the state as it now stands, not only the work you did this turn: a phase an earlier attempt completed still counts, and a tip an earlier attempt declared still counts.

Select `pass` only if all of that holds — the work then hands to the verify node. Select `fail` if a phase is incomplete, or the work is not committed, pushed, and declared. The failure output is attached when the build node is re-entered.

Alongside your verdict, submit this node's **retrospective** as its `retrospective` asset: run `blizzard runner artifact create --name retrospective` with a few honest lines on stdin — what went well, what didn't, and what the next node (or the next run) should know. The terminal retrospective node synthesizes these.
