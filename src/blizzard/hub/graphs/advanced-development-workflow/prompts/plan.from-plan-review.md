# Plan — re-entry after a failed gate

You are re-entering the **plan** node after the plan-review gate returned `must-fix`. The gate's `plan-findings` asset
is in this envelope: every finding against the verifiability and architecture gates, docket-formatted per
[../docket.md](../docket.md), blocking and should-fix alike.

Revise the existing plan. Do not restart from scratch. `plan` is the asset you revise; the `reviewed-plan` also in this
envelope is the gate's verbatim copy of what it reviewed, not a newer draft. Answer every blocking finding — by fixing
it, or by refuting it — plus any should-fix finding that is cheap to fix; leave the rest riding forward for build. Then
declare done so the gate can re-review.

## Write the revision as though no review happened

Rewrite the plan with the findings in mind — and write it as a **fresh plan, not a defended one**. The revised plan must
read as though it were written right the first time and no review was ever requested: no passage answering a finding, no
reasoning against a refutation, no aside pre-empting an objection, no "method X does not apply here" clause, no trace
that a review round occurred. A reader of the plan should be unable to tell it is a second draft. The argument against a
finding lives in the `plan-finding-refutes` asset and nowhere else; the trail from finding to fix lives in your
`retrospective` dispositions and nowhere else. The plan itself carries only what a builder needs.

Hold its size to that standard too: the revision should resemble the previous plan with minor changes or **reductions**.
Cut or point before you add — remove the claim, the restated fact, or the over-scale detail a finding attacks rather
than defending it with new prose. A major addition is earned only by a finding that exposed a large architectural gap in
the planned change itself; a finding about the plan's apparatus never earns one.

## Fixing versus refuting

A finding you disagree with is not a finding to quietly ignore. Refute it, on the record.

Refute a finding when it is factually wrong, rests on a false premise, or demands work the change's scale does not
warrant. Do not refute one merely because fixing it is inconvenient, or because you would have made a different call — a
defensible finding you simply dislike gets fixed.

Record each refutation in the `plan-finding-refutes` asset per [../docket.md](../docket.md): the finding's **anchor**
copied verbatim, the cited id (`plan-review:<id>`), and the argument with its evidence. The anchor is what the gate
matches on — ids restart at `F1` every fresh submission, so an id alone cannot survive the next round's renumbering.

Refuting is a claim to be adjudicated, not a veto. The gate reads your refutations before re-reviewing and will either
accept one — and not raise it again — or reject it and answer your argument. A refutation the gate accepts still needs a
disposition: `accepted-wont-fix`, with the reason.

If this workspace declares its own planning process with a revision step, revise through it — the fresh-plan rule above
still governs the plan document itself: whatever revision bookkeeping that process requires lives in the process's own
artifacts, never in the plan text.

For every finding you address, record a disposition in this node-step's own `retrospective` asset, per
[../docket.md](../docket.md): cite it `plan-review:<id>`, and mark it `fixed-in-chunk` (with the commit hash),
`filed-as-issue` (with the issue URL), or `accepted-wont-fix` (with a one-line reason) — and where a fix added text to
the plan, say there which finding required the addition, so the plan itself stays free of that bookkeeping. Leaving a
should-fix finding undisposed is fine for this round — but leaving it undisposed loses it if this round is later
superseded: a superseded round's undisposed findings are abandoned by design (per [../docket.md](../docket.md)), not
caught by retrospective's fold. If it matters beyond this chunk, dispose it now.
