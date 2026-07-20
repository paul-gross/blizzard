# Build — re-entry after a failed review

You are re-entering the **build** node after the review node found blocking
issues. The `review-findings` asset in this envelope records each one. Address
every finding, re-run the checks (`mise run lint`, `mise run test`) until they are
green, commit the fix, and declare done again.
