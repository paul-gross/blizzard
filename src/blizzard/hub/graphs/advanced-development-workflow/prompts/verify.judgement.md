# Verify — judgement

Render your verification verdict. Your report rides forward as the `verification-report` asset — if you have not yet run
`blizzard runner artifact create --name verification-report` with your report on stdin, do that now.

Select `pass` only if every part of the change verified through a declared method, or through one you built and recorded
here. Select `fail` if a failure remains that belongs to the build — your report rides back into the build node.

Alongside your verdict, submit this node's retrospective: run `blizzard runner artifact create --name retrospective`
with a few honest lines on stdin — what went well, what didn't, and what the next node or run should know. The terminal
retrospective node synthesizes these.
