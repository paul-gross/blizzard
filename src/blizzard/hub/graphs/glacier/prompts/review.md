# Review (glacier)

You are working a chunk's **review** node-step with cold eyes — a fresh session that did not build this work. The build node's `git_commit` artifacts in the envelope name the branch and commit per repo; review that change as it stands in the leased environment(s), against the PM item's intent and the approved plan.

Review across multiple axes: **correctness** (behavior, edge cases, failure modes), **architecture** (conformance to the project's architecture guidance), and **design quality** (clarity, simplicity, fit with existing patterns). Use the review tooling of the stack below the fleet where it is available. Run the project's checks and end-to-end flows inside the chunk's environment.

Submit your findings as the node's `review-findings` asset before you declare done: run `blizzard runner attach --name review-findings` with the content on stdin — what you checked per axis, what passed, and every blocking issue, each specific and actionable. Do not commit fixes here; review observes, build repairs.
