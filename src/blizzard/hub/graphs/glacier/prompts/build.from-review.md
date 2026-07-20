# Build — re-entry after a failed review

You are re-entering the **build** node after the multi-axes review returned `fail`. The review's `review-findings` asset is attached in this envelope: it lists the blocking issues found per axis in the previous build. Address every finding, re-run the node's checks until they are green, and commit the fix before you declare done again — the work returns to review for another cold-eyes pass.
