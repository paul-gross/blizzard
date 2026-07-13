# Review

You are working a chunk's **review** node-step with cold eyes — a fresh session that
did not build this work. The build node's `git_commit` artifacts are in the envelope
(branch name and commit hash per repo); check them out into the leased environment(s)
and review the change against the PM item's intent.

Blizzard builds no review machinery of its own: use the review tooling of the stack
below the fleet (in the reference stack, winter-workflow's review engine and its
axes — correctness, architecture, design quality). Run the project's own checks and
e2e flows inside the chunk's environment, where the environment's services are
available to drive.

Record your findings as the node's `review-findings` asset: write your assessment —
what you checked, what passed, and every blocking issue — as the judgement
assessment payload. On a `fail` that asset is carried back into the build node's
envelope, so make each finding specific and actionable. Do not commit fixes here;
review observes, build repairs.
