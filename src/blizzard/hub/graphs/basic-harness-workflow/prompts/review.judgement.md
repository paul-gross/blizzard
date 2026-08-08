# Review — judgement

Render your review verdict on the submitted work. Your findings ride forward as the `review-findings` asset — if you have not yet run `blizzard runner artifact create --name review-findings` with your findings on stdin, do that now, before you record this verdict. That asset must record how you adjudicated every entry in `review-finding-refutes`, accepted and rejected alike; a finding whose refutation you accepted is resolved and does not block `pass`.

Select `pass` if the work meets the work item's intent, is well-formed by the review axes you applied, and you found no blocking issue — the chunk proceeds to delivery. Select `fail` if any blocking issue remains; the attached `review-findings` asset is carried back into the build node's envelope, so the next build attempt can address each one.

**Converge or escalate.** Before selecting `fail`, read the round history with `blizzard runner chunk history`. Nothing bounds this loop mechanically — a judged `fail` does not consume the node's retry budget — so the bound is yours to apply. Do not bounce the work again when any of these holds:

- your blocking-finding count has not gone down from the previous round, or
- this is the third or later review round, or
- you are rejecting a refutation you already rejected in an earlier round, and neither side's argument has changed.

Raise it with `blizzard runner ask` instead, naming the round history, the findings still unresolved, and the refutations still in dispute. This lane reviews prose and convention, where two competent readers can disagree indefinitely in good faith — so a stalled argument is a human decision, not a fourth round.
