# Build — judgement

Assess the build you just completed against this node's criteria: the change
implements the PM item's intent, and the node's checks (`mise run lint`,
`mise run test`) are green with your work committed and pushed.

Select `pass` only if both hold. Select `fail` if the checks are red or the work
does not yet meet the item's intent — the failure output will be attached when the
build node is re-entered.
