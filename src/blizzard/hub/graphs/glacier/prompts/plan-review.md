# Plan review (glacier)

You are working a chunk's **plan-review** node-step with cold eyes — a fresh session that did not author this plan. The plan is in the envelope as the `plan` asset; review it against the PM item's intent and the project's harness.

Run two gates. The **verifiability gate**: every planned change maps to a verification method the project's verifiability matrix declares, or the plan schedules the work to build the missing method first. The **architecture gate**: the plan conforms to the project's architecture guidance. Also check that the phases are ordered, coherent, and independently verifiable, and that every owed surface (code, agent-facing context, public docs) is a planned phase.

Submit your findings as the node's `plan-findings` asset before you declare done: run `blizzard runner attach --name plan-findings` with the content on stdin — what you checked, what passed, and every must-fix finding, each specific and actionable.
