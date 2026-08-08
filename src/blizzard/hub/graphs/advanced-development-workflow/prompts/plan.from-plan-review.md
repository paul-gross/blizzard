# Plan — re-entry after a failed gate

You are re-entering the **plan** node after the plan-review gate returned `must-fix`. The gate's `plan-findings` asset is in this envelope: every finding against the verifiability and architecture gates, docket-formatted per [../docket.md](../docket.md), blocking and should-fix alike.

Revise the existing plan. Do not restart from scratch. Answer every blocking finding — by fixing it, or by refuting it — plus any should-fix finding that is cheap to fix; leave the rest riding forward for build. Then declare done so the gate can re-review.

## Fixing versus refuting

A finding you disagree with is not a finding to quietly ignore. Refute it, on the record.

Refute a finding when it is factually wrong, rests on a false premise, or demands work the change's scale does not warrant. Do not refute one merely because fixing it is inconvenient, or because you would have made a different call — a defensible finding you simply dislike gets fixed.

Record each refutation in the `plan-finding-refutes` asset per [../docket.md](../docket.md): the finding's **anchor** copied verbatim, the cited id (`plan-review:<id>`), and the argument with its evidence. The anchor is what the gate matches on — ids restart at `F1` every fresh submission, so an id alone cannot survive the next round's renumbering.

Refuting is a claim to be adjudicated, not a veto. The gate reads your refutations before re-reviewing and will either accept one — and not raise it again — or reject it and answer your argument. A refutation the gate accepts still needs a disposition: `accepted-wont-fix`, with the reason.

If this workspace declares its own planning process with a revision step, revise through it.

**Resolve findings by deletion first.** Cut or point before you add. Remove the claim, the mechanism passage, the restated fact, or the over-scale detail the finding attacks — do not defend it with new prose. Add text only where a finding names misdirected building: something the builder would get wrong without it. For each addition, state in one line which finding required it.

A plan that grows every round is itself a signal. If your revisions have become guard text about the plan's own criteria rather than changed substance, or if findings keep demanding detail the work's scale should not carry, stop iterating and raise it with `blizzard runner ask` instead of drafting another round.

For every finding you address, record a disposition in this node-step's own `retrospective` asset, per [../docket.md](../docket.md): cite it `plan-review:<id>`, and mark it `fixed-in-chunk` (with the commit hash), `filed-as-issue` (with the issue URL), or `accepted-wont-fix` (with a one-line reason). Leaving a should-fix finding undisposed is fine for this round — but leaving it undisposed loses it if this round is later superseded: a superseded round's undisposed findings are abandoned by design (per [../docket.md](../docket.md)), not caught by retrospective's fold. If it matters beyond this chunk, dispose it now.
