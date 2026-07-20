# Pre-push rebase (advanced-development-workflow)

You are working a chunk's **pre-push** node-step with cold eyes — the integration step before delivery. The change-set is every repo in the leased environment(s) ahead of its upstream. Your job: rebase it onto the current base branch, absorb whatever that costs, and triage how much the integration disturbed the validated work.

For each repo ahead of its upstream: fetch, then rebase the branch onto the latest base branch (`origin/master` unless the repo records another). Resolve every conflict **inside the rebase** — never abandon it for a merge, never skip a commit. Keep each resolution minimal and faithful to both sides' intent, and note every file a resolution touched.

Then run the standard procedural verifications on the rebased result: the project's linter, and the unit tests covering what the change (and any conflict resolutions) touched. Scope the test run by judgement — targeted, not the entire suite; full verification depth belongs to the verify node.

Submit the outcome as the node's `pre-push-summary` asset before you declare done: run `blizzard runner attach --name pre-push-summary` with the content on stdin — per-repo rebase result, every conflict and how it was resolved, what lint/tests ran and their results, and your severity triage — no conflicts worth naming, mechanical-only resolutions, or resolutions that made semantic choices.
