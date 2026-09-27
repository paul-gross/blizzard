# Verify — re-entry after a significant pre-push rebase

You are re-entering the **verify** node after the pre-push rebase resolved conflicts that required semantic choices. The
`pre-push-summary` asset in this envelope records each conflict and the choice made.

The branches have been rewritten since you last saw them, so the tip your last report recorded no longer reaches the
current tip: a tip-to-tip diff is mostly the base branch's own motion, not this change's. Scope this visit from the
`pre-push-summary` instead — the files each resolution touched, and `git range-diff` where the rebase reshaped the
change — and re-verify the behavior those resolutions reach through the project's declared methods.
