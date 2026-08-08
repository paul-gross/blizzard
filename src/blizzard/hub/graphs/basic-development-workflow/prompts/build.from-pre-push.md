# Build — re-entry after a significant pre-push rebase

You are re-entering the **build** node after the pre-push rebase resolved conflicts that required semantic choices, or materially reshaped the change. The `pre-push-summary` asset in this envelope records each conflict and the choice made.

The branches were rewritten by that rebase, so check what each repo carries now before you act. This lane has no separate verify node, so revalidating the rebased result happens here: pay particular attention to the behavior the resolutions touched, address anything the rebase disturbed, and re-declare each repo's tip before you declare done again.
