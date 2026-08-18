# Plan review — judgement

Render your gate verdict. Two assets must be published before it: `plan-findings` — how you adjudicated every
refutation, and a severity for every finding — and `reviewed-plan`, the plan of record: your improvements folded in on
`acceptable`, the subject plan verbatim on `must-fix`. Build implements `reviewed-plan`, so an `acceptable` verdict
without it leaves build nothing to build.

Select `acceptable` when both gates hold with no must-fix finding. Select `must-fix` only for a finding that meets the
severity anchor. A defect you can fix surgically yourself is never a reason to bounce — fold it and select `acceptable`.
A finding whose refutation you accepted is resolved: it does not block `acceptable`.

Alongside your verdict, submit this node's retrospective: run `blizzard runner artifact create --name retrospective`
with a few honest lines on stdin — what went well, what didn't, and what the next node or run should know.
