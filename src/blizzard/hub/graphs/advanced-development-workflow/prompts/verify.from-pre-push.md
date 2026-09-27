# Verify — re-entry after a significant pre-push rebase

You are re-entering the **verify** node after the pre-push rebase ruled `significant`: a resolution required a semantic
choice, the rebase materially reshaped the change, or the targeted lint and unit checks failed. The `pre-push-summary`
asset in this envelope records which, with each conflict, the choice made, and the checks' results.

The branches have been rewritten since you last saw them, so the tip your last report recorded predates the rebase:
scope this visit by the change's own commits, as *Scope this visit* directs for a rebase, and re-verify through the
project's declared methods the behavior the `pre-push-summary`'s resolutions reach. Every lint or test failure the
summary reports is a standing failure, whatever the range-diff shows: re-run it, fix it, and re-verify what the fix
reaches, alongside the resolutions.
