# Review — judgement

Render your review verdict. The `review-findings` asset must be published before it — what you reviewed per axis, every
finding, and how you adjudicated every entry in `review-finding-refutes`.

A finding whose refutation you accepted is resolved, exactly as if it had been fixed — it does not block `pass`.

Select `pass` if the work meets the plan and the item's intent with no blocking issue on any axis. Select `fail` if any
blocking issue remains; your findings ride back into the build node.

Alongside your verdict, submit this node's retrospective: run `blizzard runner artifact create --name retrospective`
with a few honest lines on stdin — what went well, what didn't, and what the next node or run should know.
