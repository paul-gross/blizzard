# Plan — re-entry after a failed gate

You are re-entering the **plan** node after the plan-review gate returned `must-fix`. The gate's `plan-findings` asset
is in this envelope — every finding, blocking and should-fix alike. The `reviewed-plan` also here is the gate's verbatim
copy of what it reviewed, not a newer draft; `plan` is the asset you revise.

Revise the existing plan — do not restart. Answer every blocking finding by fixing it or refuting it, fix any should-fix
finding that is cheap, and leave the rest riding forward for build. Then declare done so the gate can re-review.

## Write the revision as though no review happened

The revised plan must read as a fresh plan written right the first time: no passage answering a finding, no reasoning
against a refutation, no "method X does not apply here" clause, no trace that a review round occurred. The argument
against a finding lives in the `plan-finding-refutes` asset and nowhere else; the trail from finding to fix lives in
your `retrospective` dispositions and nowhere else. The plan itself carries only what a builder needs.

Hold its size to that standard too: the revision should resemble the previous plan with minor changes or **reductions**.
Cut or point before you add — remove the claim, restated fact, or over-scale detail a finding attacks rather than
defending it with new prose. Only a finding exposing a large architectural gap in the planned change itself earns a
major addition; a finding about the plan's apparatus never does.

If this workspace declares its own planning process with a revision step, revise through it — the fresh-plan rule still
governs the plan text itself: that process's revision bookkeeping lives in its own artifacts, never in the plan.

## Fixing versus refuting

Refute a finding when it is factually wrong, rests on a false premise, or demands work the change's scale does not
warrant — never merely because fixing it is inconvenient or you would have made a different call. A defensible finding
you dislike gets fixed; a finding you disagree with gets refuted on the record, not quietly ignored.

Record each refutation in the `plan-finding-refutes` asset: the finding's **anchor** copied verbatim, the cited id
(`plan-review:<id>`), and the argument with its evidence. The gate matches on the anchor — ids restart at `F1` every
fresh submission, so an id alone cannot survive renumbering. Refuting is a claim the gate adjudicates, never a veto: it
will accept and not raise the finding again, or reject and answer your argument. An accepted refutation still needs a
disposition — `accepted-wont-fix`, with the reason.

For every finding you address, record a disposition in this node-step's own `retrospective` asset: cite it
`plan-review:<id>` and mark it `fixed-in-chunk` (with the commit hash), `filed-as-issue` (with the issue URL), or
`accepted-wont-fix` (with a one-line reason). Where a fix added text to the plan, say there which finding required the
addition, so the plan stays free of that bookkeeping. Leaving a should-fix finding undisposed is fine for this round —
but a superseded round's undisposed findings are abandoned by design, not caught by retrospective's fold, so if it
matters beyond this chunk, dispose it now.

The refutation and disposition record shapes above are restated from the docket; read the whole thing with
`blizzard runner artifact get docket --scope graph --content`. If that command fails — any error, rather than the
docket's text — proceed on the restatement above and do not retry: it is what you need for this node.
