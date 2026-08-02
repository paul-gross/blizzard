# Build — re-entry after a failed review

You are re-entering the **build** node after the multi-axes review returned `fail`. The review's `review-findings` asset is attached in this envelope: it lists every finding found per axis in the previous build, docket-formatted per [../docket.md](../docket.md) — blocking and should-fix alike. Address every blocking finding and commit the fix before you declare done again — the work returns to review for another cold-eyes pass. Fix a should-fix finding too where it's cheap; where it isn't, leave it.

For every finding you address, record a disposition in this node-step's own `retrospective` asset, per [../docket.md](../docket.md): cite it `review:<id>`, and mark it `fixed-in-chunk` (with the commit hash), `filed-as-issue` (with the issue URL), or `accepted-wont-fix` (with a one-line reason). Leaving a should-fix finding undisposed is fine — the retrospective node folds the docket and catches whatever is still open.
