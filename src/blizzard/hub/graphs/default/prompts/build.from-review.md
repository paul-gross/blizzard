# Build — re-entry after a failed review

You are re-entering the **build** node after the review node returned `fail`. The
review's `review-findings` asset is attached in this envelope: it lists the blocking
issues the reviewer found in the previous build. Address every finding and commit
the fix before you declare done again — the work returns to review for another
cold-eyes pass.
