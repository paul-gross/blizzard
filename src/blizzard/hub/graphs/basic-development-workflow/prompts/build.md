# Build

You are working a chunk's **build** node-step. The chunk wraps one or more PM
items (the envelope carries their pointers); read them through the runner's
PM-item proxy, implement the change in the leased environment(s).

This is the lightweight lane: there is no planning node ahead of this one. If the
approach needs working out, think it through inline as part of building — you may
plan, but no plan artifact is produced and nothing here is gated on one. There is
also no separate verify node behind this one: build and verification are ONE node
here. Satisfy the PM item's intent before you declare done — treat that as this
node's own finale, not a formality deferred to a later step.

Commit your work to a branch and push it — the branch and commit are the node's
`git_commit` artifact (the hub stores the reference, never the code). When the
work meets the item's intent, declare done; the runner will resume you with the
judgement prompt to elicit your verdict.
