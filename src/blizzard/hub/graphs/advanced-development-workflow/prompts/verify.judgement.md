# Verify — judgement

Render your verification verdict. The `verification-report` asset must be published before it.

Select `pass` only if every part of the change verified through a declared method. Select `fail` if a failure remains
that belongs to the build — your report rides back into the build node.

Alongside your verdict, submit this node's retrospective: run `blizzard runner artifact create --name retrospective`
with a few honest lines on stdin — what went well, what didn't, and what the next node or run should know.
