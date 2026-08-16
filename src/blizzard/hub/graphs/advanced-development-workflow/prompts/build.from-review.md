# Build — re-entry after a failed review

You are re-entering the **build** node after the multi-axes review returned `fail`. The review's `review-findings` asset
is in this envelope — every finding per axis, blocking and should-fix alike.

Your commits are intact on the feature branch; nothing has landed. Answer every blocking finding — by fixing it or
refuting it — and commit before you declare done again; the work returns to review for another cold-eyes pass. Fix a
should-fix finding too where it is cheap; where it isn't, leave it. Check each finding against the code as it now stands
first — a finding an earlier attempt already resolved needs a disposition, not a second fix.

## Fixing versus refuting

Refute a finding when it is factually wrong, rests on a false premise, or demands work the change's scale does not
warrant — never merely because fixing it is inconvenient or you would have made a different call. A defensible finding
you dislike gets fixed; a finding you disagree with gets refuted on the record, not quietly ignored. The channel exists
because `review` is a **full cold read every pass**, not a delta: without a refutation on the record, a finding you
deliberately declined is re-discovered and re-raised every round, forever.

Record each refutation in the `review-finding-refutes` asset: the finding's **anchor** copied verbatim, the cited id
(`review:<id>`), and the argument with its evidence. The reviewer matches on the anchor — ids restart at `F1` every
fresh submission, so an id alone cannot survive renumbering. Refuting is a claim the reviewer adjudicates, never a veto:
it will accept and not raise the finding again, or reject and answer your argument. An accepted refutation still needs a
disposition — `accepted-wont-fix`, with the reason.

For every finding you address, record a disposition in this node-step's own `retrospective` asset: cite it `review:<id>`
and mark it `fixed-in-chunk` (with the commit hash), `filed-as-issue` (with the issue URL), or `accepted-wont-fix` (with
a one-line reason). Leaving a should-fix finding undisposed is fine for this round — but a superseded round's undisposed
findings are abandoned by design, not caught by retrospective's fold, so if it matters beyond this chunk, dispose it
now.
