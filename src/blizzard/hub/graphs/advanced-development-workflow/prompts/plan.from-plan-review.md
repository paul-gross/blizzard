# Plan — re-entry after a failed gate

You are re-entering the **plan** node after the plan-review gate returned `must-fix`. The gate's `plan-findings` asset
is in this envelope — every finding, blocking and should-fix alike. The `reviewed-plan` also here is the gate's verbatim
copy of what it reviewed, not a newer draft; `plan` is the asset you revise.

Revise the existing plan — do not restart. Answer every blocking finding by fixing or refuting. The plan returns to the
gate for re-review.

Write the revision as though no review happened: no passage answering a finding, no trace that a review round occurred.
The argument against a finding lives in the `plan-finding-refutes` asset, nowhere else. Cut or point before you add —
the revision should resemble the previous plan with minor changes or reductions.

## Fixing versus refuting

Refute a finding when it is factually wrong, rests on a false premise, or demands work the change's scale does not
warrant — never merely because fixing it is inconvenient. A declined finding without a refutation on the record can be
re-raised.

Record each refutation in the `plan-finding-refutes` asset: the finding's **anchor** copied verbatim, the cited id
(`plan-review:<id>`), and the argument. Refuting is a claim the gate adjudicates, never a veto. The full record shape is
in the docket: `blizzard runner artifact get docket --scope graph --content`.
