# Plan — re-entry after a failed gate

You are re-entering the **plan** node after the plan-review gate returned `must-fix`. The gate's `plan-findings` asset is attached in this envelope: it lists every blocking finding against the verifiability and architecture gates. Revise the plan to address each finding — do not restart from scratch — and declare done so the gate can re-review.

Address findings by simplifying where you can — cut or tighten before you add. A plan that grows on every round is itself a signal: if your revisions have become guard text about the plan's own criteria rather than changed substance, stop iterating and raise it with `blizzard runner ask` instead of drafting another round.
