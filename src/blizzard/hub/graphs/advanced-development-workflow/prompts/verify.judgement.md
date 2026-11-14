# Verify — judgement

Render your verification verdict. The `verification-report` asset must be published before it.

Select `pass` only if every part of the change verified through a declared method or is explicitly waived within scope
by a person's answer or note — on a re-visit, use the prior report's standing results together with what this visit
re-ran, under the scoping decision the new report records. A waiver does not make a failed or not-run method pass:
identify the waiver and preserve the actual result. Select `fail` on any unwaived failure, including a new or unrelated
acceptance failure — your report rides back into the build node, which repairs it.

Alongside your verdict, submit this node's retrospective: run `blizzard runner artifact create --name retrospective`
with a few honest lines on stdin — what went well, what didn't, and what the next node or run should know.
