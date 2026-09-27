# Verify — re-entry after a significant pre-push rebase

You are re-entering the **verify** node after the pre-push rebase resolved conflicts that required semantic choices. The
`pre-push-summary` asset in this envelope records each conflict and the choice made.

The branches have been rewritten since you last saw them, so the tip your last report recorded predates the rebase:
scope this visit by the change's own commits, as *Scope this visit* directs for a rebase, and re-verify through the
project's declared methods the behavior the `pre-push-summary`'s resolutions reach.
