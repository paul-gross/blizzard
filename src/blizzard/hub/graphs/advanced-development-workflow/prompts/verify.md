# Verify (advanced-development-workflow)

You are working a chunk's **verify** node-step — the verify finale.

You may be entering after build, after a pre-push rebase that made semantic choices, or retrying after a crash. Run
`blizzard runner artifact list` and check the branch state in each repo first: you verify the change **as it now
stands**, not as an earlier node described it. A `verification-report` from an earlier attempt tells you what was
already exercised; it does not excuse you from re-exercising what has changed since.

Verify the change **through a method the project declares**, exercising real runtime behavior in the leased
environment(s), where the environment's services are available to drive. A green build or type-check is not a
verification. When no declared method covers the change, **build the missing method** — add or extend a durable
verification method and record it alongside the project's others — rather than improvising a one-off pass. Fix what
verification surfaces and re-verify until the method passes.

Submit what you exercised, what passed, and anything you could not close as the node's `verification-report` asset
before you declare done: run `blizzard runner artifact create --name verification-report` with the content on stdin.
