# Plan review — judgement

Render your gate verdict. Two assets must be published before it:

- `plan-findings` — run `blizzard runner artifact create --name plan-findings` with your findings on stdin if you have not already. It must record how you adjudicated every entry in `plan-finding-refutes`, accepted and rejected alike, and a severity for every finding — `blocking`, `should-fix`, or (only with an `acceptable` verdict) `folded`.
- `reviewed-plan` — run `blizzard runner artifact create --name reviewed-plan` with the plan of record on stdin: the plan with your improvement-tier findings folded in on `acceptable`, or the subject plan verbatim on `must-fix`. Build implements this asset, so an `acceptable` verdict without it leaves build nothing to build.

Select `acceptable` when both gates hold with no must-fix finding — every improvement-tier finding is folded into `reviewed-plan` (or rides forward as `should-fix`), and the chunk proceeds to build on it. Select `must-fix` only for a finding that meets the severity anchor: building the plan as written would produce a wrong, unverifiable, or architecture-violating change, and repairing it means remaking a decision the plan's author owns. A defect you can fix surgically yourself is never a reason to bounce — fold it and select `acceptable`.

A finding whose refutation you accepted is **resolved**, exactly as if it had been fixed. It does not block `acceptable`, and it does not count toward the blocking tally.

Alongside your verdict, submit this node's **retrospective** as its `retrospective` asset: run `blizzard runner artifact create --name retrospective` with a few honest lines on stdin — what went well, what didn't, and what the next node (or the next run) should know. The terminal retrospective node synthesizes these.
