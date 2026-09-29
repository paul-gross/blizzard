# Build — re-entry after a failed review

You are re-entering the **build** node after the review returned `fail`. The review's `review-findings` asset is in this
envelope — every finding per axis, blocking and should-fix alike.

Answer every blocking finding by fixing or refuting. The work returns to review for another pass.

Do not rebase or merge the base branch while repairing: verify and review re-visit by diffing from the tip they last
judged, and base motion folded into that delta buries the repair in it. Integration is `pre-push`'s.

A finding that raises a surviving mutant follows the project's survivor classification. To close a real gap, add the
assertion that kills the mutant and name that mutant in the commit that adds it — never in the code, since mutant names
renumber as code moves. For an equivalent mutant, record the equivalence with its pattern: the project's in-source
marker where the classification allows one, otherwise a `review-finding-refutes` entry.

## Fixing versus refuting

Refute a finding when it is factually wrong, rests on a false premise, or demands work the change's scale does not
warrant — never merely because fixing it is inconvenient. A declined finding without a refutation on the record can be
re-raised.

Record each refutation in the `review-finding-refutes` asset: the finding's **anchor** copied verbatim, the cited id
(`review:<id>`), and the argument. Refuting is a claim the reviewer adjudicates, never a veto. The full record shape is
in the docket: `blizzard runner artifact get docket --scope graph --content`. If that read fails or comes back empty,
proceed on the restatement above.
