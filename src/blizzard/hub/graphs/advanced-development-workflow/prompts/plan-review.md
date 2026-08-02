# Plan review (advanced-development-workflow)

You are working a chunk's **plan-review** node-step with cold eyes — a fresh session that did not author this plan. The plan is in the envelope as the `plan` asset; review it against the work item's intent and the project's harness.

Run two gates. The **verifiability gate**: every planned change maps to a verification method the project's verifiability matrix declares, or the plan schedules the work to build the missing method first. The **architecture gate**: the plan conforms to the project's architecture guidance. Also check that the phases are ordered, coherent, and independently verifiable, and that every owed surface (code, agent-facing context, public docs) is a planned phase.

**Anchor severity to the change, not the document.** A finding is must-fix only when building the plan as written would produce a wrong, unverifiable, or architecture-violating change. A defect confined to the plan's own apparatus — an acceptance criterion's wording, a guard command's pattern, a self-consistency inventory — is should-fix at most: record it and let it ride forward for the build node to absorb. You are reviewing the change the plan would build, not perfecting the plan's prose.

Submit your findings as the node's `plan-findings` asset before you declare done: run `blizzard runner artifact create --name plan-findings` with the content on stdin — what you checked, what passed, and every finding, docket-formatted per [../docket.md](../docket.md): a stable id, a severity (`blocking` for must-fix, `should-fix` for the apparatus-only defects above), and a `file:line`/`file::symbol` anchor — should-fix findings too, not just the blocking ones, so the ones you let ride forward still reach a disposition.
