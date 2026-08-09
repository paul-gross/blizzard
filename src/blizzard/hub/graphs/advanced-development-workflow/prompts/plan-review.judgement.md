# Plan review — judgement

Render your gate verdict. Your findings ride forward as the `plan-findings` asset — if you have not yet run `blizzard runner artifact create --name plan-findings` with your findings on stdin, do that now, before you record this verdict. That asset must record how you adjudicated every entry in `plan-finding-refutes`, accepted and rejected alike.

Select `pass` only if both gates hold with no must-fix finding — the chunk proceeds to build on this plan. Select `must-fix` only for a finding that meets the severity anchor: building the plan as written would produce a wrong, unverifiable, or architecture-violating change. A defect in the plan's own apparatus is not blocking — record it as should-fix and pass; the build node absorbs it.

A finding whose refutation you accepted is **resolved**, exactly as if it had been fixed. It does not block `pass`, and it does not count toward the blocking tally below.

Alongside your verdict, submit this node's **retrospective** as its `retrospective` asset: run `blizzard runner artifact create --name retrospective` with a few honest lines on stdin — what went well, what didn't, and what the next node (or the next run) should know. The terminal retrospective node synthesizes these.
