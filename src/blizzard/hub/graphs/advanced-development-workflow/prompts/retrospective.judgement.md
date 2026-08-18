# Retrospective — judgement

Confirm the retrospective is written as the `retrospective` asset with all required sections — above all **Landing
Verification** — and that this workspace's post-delivery convention, if it declares one, was carried out or its failure
reported plainly.

Select `recorded` when the landing verification found nothing wrong, or found only a red merge-commit gate result and/or
an open work item — each is logged as a finding, and neither alone withholds `recorded`. The chunk closes.

Select `delivery-incomplete` when the landing verification found a real discrepancy: a declared sha not reachable from
its repo's base branch, or a repo's PR unmerged. Name the specific discrepancy in your `retrospective` asset before
selecting it; `resolve` reads it from there.

**Loop bound.** Before selecting `delivery-incomplete`, read `blizzard runner chunk history`. If a `delivery-incomplete`
transition has already left this node once for this chunk, do not select it again: record the second discrepancy as a
finding and escalate with `blizzard runner ask`, so a human resolves it rather than the cycle looping.
