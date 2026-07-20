# Build — re-entry after a significant pre-push rebase

You are re-entering the **build** node after the pre-push rebase resolved
conflicts that required semantic choices (or the rebase materially reshaped the
change). The `pre-push-summary` asset in this envelope records each conflict and
the choice made. This lane has no separate verify node, so re-earning the checks
happens here: re-run `mise run lint` and `mise run test` on the rebased result,
with particular attention to the behavior the resolutions touched, and address
anything the rebase disturbed before you declare done again.
