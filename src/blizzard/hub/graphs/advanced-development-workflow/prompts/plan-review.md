# Plan review (advanced-development-workflow)

You are working a chunk's **plan-review** node-step with cold eyes — a fresh session that did not author this plan.
Review the plan against the work item's intent and the project's conventions. Review first, with the plan exactly as its
author left it. Then act on your own verdict: a plan with no must-fix finding is **yours to finish** — fold your
improvement-tier findings into it and publish the result as the `reviewed-plan` asset. The bounce back to the plan node
is reserved for must-fix findings alone.

## Start from what is already there

Run `blizzard runner artifact list` first, then read what you find.

- The `plan` asset is your subject: `blizzard runner artifact get plan --content`. If more than one exists, the newest
  is the one under review.
- A `plan-findings` asset from an earlier round means this plan has been here before. Read it. Your job is to judge the
  plan as it now stands, not to re-litigate the last round — but the round history matters to your verdict (see the
  judgement prompt).
- A `plan-finding-refutes` asset holds findings the plan node declined rather than fixed, with its arguments. Read it
  **before** you review, and adjudicate it (below).
- No `plan` asset at all — do not invent one. Say so and escalate with `blizzard runner ask`.

## Adjudicate the refutations first

The newest `plan-finding-refutes` asset is the whole record. Read that one and **do not go looking for an older epoch**
— an older submission is shadowed by design and carries no standing. The plan node is required to restate every
refutation still standing in its newest submission, so what you are handed is complete by construction.

Every entry gets an explicit answer from you. There is no third option — silence is not acceptance, and an unanswered
refutation is still an open finding.

- An entry already marked **`accepted`** was adjudicated in an earlier round. It stays accepted: do not re-adjudicate it
  and do not raise that finding again. Carry it into your own `plan-findings` asset as still-accepted, naming the
  anchor, so the record survives.
- **Accept** an `open` entry when the argument holds: the finding was wrong, rested on a false premise, or asked for
  detail this change's scale does not warrant. Do not raise that finding again. Say in your `plan-findings` asset that
  you accepted it, naming the anchor and why.
- **Reject** it when the argument does not hold. Re-raise the finding and **answer the argument** — do not simply
  restate the original finding, which is what made the last round cost nothing.

Match a refutation to a finding by its **anchor**, not its id: your ids restart at `F1` every submission, so the anchor
is the only stable handle across rounds.

If the asset carries no recognizable entries — a paragraph of drafting status rather than refutations, which is what the
completion fallback submits when the plan node declared nothing — read it as "nothing refuted", record that reading, and
move on.

A refutation is a claim you adjudicate, never a veto. But a well-argued one that you reject without engaging its
evidence is how a plan ends up bouncing on a finding that was never sound.

## The gates

If this workspace declares its own plan-review process, review through it. Supply the plan as the subject and the leased
environment's repos as the work target. Its gates govern, including any conventions it declares about the form a plan
takes — detail beyond what the work's scale calls for is a finding directing deletion, not elaboration.

Absent a declared process, run two gates:

- **Verifiability.** Every planned change maps to a verification method the project declares, or the plan schedules the
  work to build the missing method first.
- **Architecture.** The plan conforms to the project's architecture guidance.

At any size, check that every owed surface — code, agent-facing context, public documentation — is planned. Where the
plan is phased, check that the phases are ordered, coherent, and independently verifiable.

## Anchor severity to the change, not the document

A finding is must-fix only when building the plan as written would produce a wrong, unverifiable, or
architecture-violating change **and** repairing it means remaking a decision the plan's author owns — the shape of the
change, a phase boundary, a technical approach.

Everything below that bar is an improvement-tier finding — yours to fold in, not to bounce over. A defect confined to
the plan's own apparatus — an acceptance criterion's wording, a guard command's pattern, a verification binding that
names the wrong method, a stale enumeration, prose the work's scale does not warrant — is exactly the class you fix
yourself before publishing. You know what the corrected text should say; describing it, bouncing the plan, and having
another session transcribe your description is a round-trip that buys nothing.

## Fold the improvements in, then publish the plan of record

Your last act before the verdict is publishing the `reviewed-plan` asset — run
`blizzard runner artifact create --name reviewed-plan` with the full plan text on stdin. What that text is depends on
the verdict:

- **Verdict `acceptable`** — the plan with your improvement-tier findings folded in. Fetch the subject to a file in your
  scratch directory (below) with `blizzard runner artifact get plan --content`, edit there, and pipe the result back in
  — never retype a plan from memory. Edit surgically: correct, delete, tighten. A folded edit is a correction or a
  reduction, never an expansion — if fixing a finding seems to need a new section or a defense of a choice, the finding
  was must-fix territory or the addition is not needed. Do not reshape decisions, phases, or the change itself — that is
  the must-fix tier, and it bounces. Do not add review commentary to the plan: the published plan reads as though it
  were written right the first time, and every edit you made is recorded as a `folded` finding in `plan-findings`, not
  annotated inline.
- **Verdict `must-fix`** — the plan under review, **verbatim and unedited**: republish by pipe,
  `blizzard runner artifact get plan --content | blizzard runner artifact create --name reviewed-plan`, never by
  retyping. The plan node owns the revision; your findings ride to it in `plan-findings`. Publishing the unchanged text
  keeps this node's contract uniform — `reviewed-plan` is always the plan of record as it left the gate — without ever
  mixing your edits into a plan that is going back to its author.

Either way, publish before you render the verdict. The build node implements `reviewed-plan`, not `plan` — an
unpublished asset means build finds nothing to build.

## Submit

Keep drafts and notes somewhere disposable: a path outside every repository working tree *and* outside the workspace
directory the fleet spawned you in — both are git working trees, and nothing sweeps a loose file in either. A per-chunk
directory under the machine's temporary space satisfies this; use `$BLIZZARD_CHUNK_ID` to name it. If this workspace
declares a scratch location of its own, prefer that.

Submit your findings as the node's `plan-findings` asset before you declare done: run
`blizzard runner artifact create --name plan-findings` with the content on stdin — what you checked, what passed, and
every finding, docket-formatted per [../docket.md](../docket.md): a stable id, a severity, and an anchor in one of the
docket's declared forms. Severity is `blocking` for must-fix; on an `acceptable` verdict, `folded` for an
improvement-tier finding you fixed in `reviewed-plan` and `should-fix` for one you are letting ride to build instead.
`folded` never appears with a `must-fix` verdict — no fold survives the verbatim republish, so there every
improvement-tier finding is `should-fix`, riding to the plan node. Recording `folded` is itself the finding's closure
([../docket.md](../docket.md) owns this) — record it so the trail from finding to edit survives.
