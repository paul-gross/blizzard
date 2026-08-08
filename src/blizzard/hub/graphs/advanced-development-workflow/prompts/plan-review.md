# Plan review (advanced-development-workflow)

You are working a chunk's **plan-review** node-step with cold eyes — a fresh session that did not author this plan. Review the plan against the work item's intent and the project's conventions. Do not edit the plan; record findings and let the plan node revise.

## Start from what is already there

Run `blizzard runner artifact list` first, then read what you find.

- The `plan` asset is your subject: `blizzard runner artifact get plan --content`. If more than one exists, the newest is the one under review.
- A `plan-findings` asset from an earlier round means this plan has been here before. Read it. Your job is to judge the plan as it now stands, not to re-litigate the last round — but the round history matters to your verdict (see the judgement prompt).
- A `plan-finding-refutes` asset holds findings the plan node declined rather than fixed, with its arguments. Read it **before** you review, and adjudicate it (below).
- No `plan` asset at all — do not invent one. Say so and escalate with `blizzard runner ask`.

## Adjudicate the refutations first

Every entry in `plan-finding-refutes` gets an explicit answer from you. There is no third option — silence is not acceptance, and an unanswered refutation is still an open finding.

- **Accept it** when the argument holds: the finding was wrong, rested on a false premise, or asked for detail this change's scale does not warrant. Do not raise that finding again, this round or any later one. Say in your `plan-findings` asset that you accepted it, naming the anchor and why.
- **Reject it** when the argument does not hold. Re-raise the finding and **answer the argument** — do not simply restate the original finding, which is what made the last round cost nothing.

Match a refutation to a finding by its **anchor**, not its id: your ids restart at `F1` every submission, so the anchor is the only stable handle across rounds.

A refutation is a claim you adjudicate, never a veto. But a well-argued one that you reject without engaging its evidence is how a plan ends up bouncing on a finding that was never sound.

## The gates

If this workspace declares its own plan-review process, review through it. Supply the plan as the subject and the leased environment's repos as the work target. Its gates govern, including any conventions it declares about the form a plan takes — detail beyond what the work's scale calls for is a finding directing deletion, not elaboration.

Absent a declared process, run two gates:

- **Verifiability.** Every planned change maps to a verification method the project declares, or the plan schedules the work to build the missing method first.
- **Architecture.** The plan conforms to the project's architecture guidance.

At any size, check that every owed surface — code, agent-facing context, public documentation — is planned. Where the plan is phased, check that the phases are ordered, coherent, and independently verifiable.

## Anchor severity to the change, not the document

A finding is must-fix only when building the plan as written would produce a wrong, unverifiable, or architecture-violating change.

A defect confined to the plan's own apparatus — an acceptance criterion's wording, a guard command's pattern, a self-consistency inventory — is should-fix at most. Record it and let it ride forward for the build node to absorb. You are reviewing the change the plan would build, not perfecting the plan's prose.

## Submit

Keep drafts and notes out of the repos' working trees and out of the environment root; nothing sweeps loose files there.

Submit your findings as the node's `plan-findings` asset before you declare done: run `blizzard runner artifact create --name plan-findings` with the content on stdin — what you checked, what passed, and every finding, docket-formatted per [../docket.md](../docket.md): a stable id, a severity (`blocking` for must-fix, `should-fix` for the apparatus-only defects above), and a `file:line` or `file::symbol` anchor. Record should-fix findings too, not just blocking ones — a finding you let ride forward only reaches a disposition if it is written down.
