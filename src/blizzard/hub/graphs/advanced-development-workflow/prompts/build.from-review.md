# Build — re-entry after a failed review

You are re-entering the **build** node after the multi-axes review returned `fail`. The review's `review-findings` asset
is in this envelope: every finding per axis, docket-formatted per [../docket.md](../docket.md), blocking and should-fix
alike.

Your commits are intact on the feature branch; nothing has landed. Answer every blocking finding — by fixing it, or by
refuting it — and commit before you declare done again; the work returns to review for another cold-eyes pass. Fix a
should-fix finding too where it is cheap; where it isn't, leave it.

Check each finding against the code as it now stands before fixing it. A finding an earlier attempt already resolved
needs a disposition, not a second fix.

## Fixing versus refuting

A finding you disagree with is not a finding to quietly ignore. Refute it, on the record.

Refute a finding when it is factually wrong, rests on a false premise, or demands work the change's scale does not
warrant. Do not refute one merely because fixing it is inconvenient, or because you would have made a different call — a
defensible finding you simply dislike gets fixed.

This channel exists because `review` is a **full cold read every pass**, not a delta. Without a refutation on the
record, a finding you deliberately declined is re-discovered and re-raised on every round, forever.

Record each refutation in the `review-finding-refutes` asset per [../docket.md](../docket.md): the finding's **anchor**
copied verbatim, the cited id (`review:<id>`), and the argument with its evidence. The anchor is what the reviewer
matches on — ids restart at `F1` every fresh submission, so an id alone cannot survive the next round's renumbering.

Refuting is a claim to be adjudicated, not a veto. The reviewer reads your refutations before re-reviewing and will
either accept one — and not raise it again — or reject it and answer your argument. A refutation the reviewer accepts
still needs a disposition: `accepted-wont-fix`, with the reason.

For every finding you address, record a disposition in this node-step's own `retrospective` asset, per
[../docket.md](../docket.md): cite it `review:<id>`, and mark it `fixed-in-chunk` (with the commit hash),
`filed-as-issue` (with the issue URL), or `accepted-wont-fix` (with a one-line reason). Leaving a should-fix finding
undisposed is fine for this round — but leaving it undisposed loses it if this round is later superseded: a superseded
round's undisposed findings are abandoned by design (per [../docket.md](../docket.md)), not caught by retrospective's
fold. If it matters beyond this chunk, dispose it now.
