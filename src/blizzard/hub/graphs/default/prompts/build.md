# Build

You are working a chunk's **build** node-step. The chunk wraps one or more PM
items (the envelope carries their pointers); read them through the runner's
PM-item proxy, implement the change in the leased environment(s), and run the
node's checks (`mise run lint`, `mise run test`) before you declare done.

Commit your work to a branch and push it — the branch and commit are the node's
`git_commit` artifact (the hub stores the reference, never the code). When the
checks are green and the work meets the item's intent, declare done; the runner
will resume you with the judgement prompt to elicit your verdict.
