# Build — re-entry after a failed review

You are re-entering the **build** node after the review returned `fail`. The review's `review-findings` asset is in this
envelope — every finding per axis, blocking and should-fix alike.

Answer every blocking finding by fixing or refuting. The work returns to review for another pass.

## Fixing versus refuting

Refute a finding when it is factually wrong, rests on a false premise, or demands work the change's scale does not
warrant — never merely because fixing it is inconvenient. A declined finding without a refutation on the record can be
re-raised.

Record each refutation in the `review-finding-refutes` asset: the finding's **anchor** copied verbatim, the cited id
(`review:<id>`), and the argument. Refuting is a claim the reviewer adjudicates, never a veto. The full record shape is
in the docket: `blizzard runner artifact get docket --scope graph --content`.
