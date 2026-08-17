# The findings docket

The shared format for every finding this graph records and every disposition that closes one, and the canonical
definition of both. This graph bakes this file into its mint, so a worker holding a lease can read the whole format on
demand with `blizzard runner artifact get docket --scope graph --content`. Each prompt also restates the slice its own
reader needs, so a node's work never depends on making that call.

The docket is built on the chunk's artifact series — append-only per node and name, with reads resolving to the newest
entry: a docket entry lives inside a `plan-findings` or `review-findings` asset; a disposition lives inside the
responding node's own `retrospective` asset.

## Docket entry

Every finding recorded in a `plan-findings` or `review-findings` asset carries:

- **id** — stable *within this asset submission*: `F1`, `F2`, … A fresh cold-eyes pass after a bounce is a new
  submission and restarts at `F1`; it does not continue a prior round's numbering.
- **severity** — `blocking` or `should-fix`. A `plan-findings` entry may instead carry `folded`: an improvement-tier
  finding the gate already fixed in the `reviewed-plan` it published under an `acceptable` verdict. Recording `folded`
  is itself the closure — no node ever owes it a disposition. On a `must-fix` verdict no fold survives the verbatim
  republish, so an improvement-tier finding is recorded `should-fix` there, never `folded`. (`plan-review.md` calls its
  blocking tier "must-fix" in prose — same value, `blocking`, in the entry.)
- **anchor** — `<repo>/<path>:<line>` or `<repo>/<path>::<symbol>`; the repo prefix keeps the anchor unambiguous once a
  chunk spans more than one repo. A finding whose target is a chunk asset rather than a repo file — the plan-apparatus
  case — anchors as `<asset-name>::<section>`, e.g. `plan::Acceptance criteria`.
- a one- or two-sentence description, specific and actionable — what's wrong, not just where.

Example, inside a `review-findings` asset:

```text
F1 — should-fix — payments-api/src/payments/worker.py:142
  Retry counter isn't reset after a successful heartbeat, so a flaky-then-recovered
  worker still escalates on its next transient failure.
```

## Refutation record

A finding is answered one of two ways: **fixed**, or **refuted**. A refutation says the finding should not be acted on
at all — it is factually wrong, rests on a false premise, or demands work the change's scale does not warrant. It is not
"I would rather not."

The responding node records its refutations in a dedicated asset submitted alongside its usual work — `plan` submits
`plan-finding-refutes`, `build` submits `review-finding-refutes`. Each entry:

- **anchor** — the finding's anchor, copied verbatim.
- **cited id** — `<node>:<id>` from the round being answered, e.g. `review:F2`.
- **the argument** — why the finding is wrong, with evidence: the code, the command, the fact it missed.

**The anchor is what matches, not the id.** Ids are stable only within one submission and restart at `F1` on a fresh
cold pass, so the id records which round was answered; the anchor is what the reviewer keys on.

**The newest submission is the entire record.** Reads resolve to the newest entry per node and name, so a later
submission replaces the earlier one rather than adding to it. Every submission therefore restates **every refutation
still standing** — including ones a gate already accepted — each marked with the round it was first raised in and its
outcome so far (`open` or `accepted`). An entry is dropped only when the finding it answers is genuinely dead: fixed, or
withdrawn by the gate. A round that fixed everything must not submit a bare "nothing to refute" while earlier
refutations still stand — that submission drops them, and the findings return on the next cold pass.

The gate reads **only** the newest submission — an older epoch is shadowed by design and carries no standing — and must
resolve every entry explicitly: accept it and not re-raise, or reject it and re-raise with an answer to the argument. An
entry already marked `accepted` stays accepted and is not re-adjudicated. Silence is not acceptance: an unanswered
refutation is still an open finding. Refuting is a claim to be adjudicated, never a veto, and an accepted refutation
still needs a disposition (`accepted-wont-fix`).

The asset is submitted on every completion, even when nothing was refuted — an explicit "nothing to refute" tells the
gate the channel was considered; an absent asset is ambiguous. An asset carrying no recognizable entries — in practice
the judgement assessment submitted by the completion fallback — is read as "nothing refuted", recorded, and not an
error.

There is deliberately **no refutation channel for verification**: a failed verification method is a mechanical fact, not
a judgement to argue with — the answer is to fix the change or fix the method.

## Disposition record

A node re-entering to address a docket records one disposition per finding it addresses, inside **its own**
`retrospective` asset. Each disposition:

- cites the finding as `<node>:<id>` — the producing node's name plus the id from the asset attached to *this*
  node-step's envelope, e.g. `review:F1`.
- is exactly one of `fixed-in-chunk` (plus the commit hash), `filed-as-issue` (plus the issue URL), or
  `accepted-wont-fix` (plus a one-line reason).

A `folded` finding is the one exception: the gate already fixed it, so no node records a disposition — the fold table
carries `folded` in its disposition column, closed by construction.

Disposing every `blocking` finding is required to clear the bounce. Disposing a `should-fix` finding is optional — but a
superseded round's undisposed findings are abandoned by design (below), so a finding that matters beyond this chunk is
disposed now (`filed-as-issue` or `accepted-wont-fix`) rather than left to a fold that will not see it.

## The retrospective fold

**Supersession is authoritative.** The fold enumerates every id in the **newest** `plan-findings` and the **newest**
`review-findings` asset only; an earlier round is superseded and out of the fold entirely, whether or not its findings
were ever disposed. This is deliberate: the next review is a full cold pass over the change as it stands, not a delta,
so a defect still present is re-reported under a new id in the newest asset, where the fold sees it. When a chunk had a
superseded round, the fold table names it, so a reader sees the supersession rather than a silent absence.

Each id is matched against disposition records anywhere in the chunk's node `retrospective` assets, then:

- a matched id is closed — its disposition carries into the fold table.
- an unmatched `should-fix` id whose target is a real repo file, describing a defect still present in the change, is
  open — a forge issue is filed for it, following the workspace's own issue-filing convention if it declares one (skill,
  format, label set), otherwise a plain `gh issue create` run from inside that finding's own repo worktree — the
  anchor's repo segment names it — and the filing is recorded as its disposition (`filed-as-issue`, with the URL). The
  filing *is* the disposition.
- an unmatched `should-fix` id whose target is an **immutable artifact** — in practice a plan-apparatus finding against
  the consumed plan asset, already consumed before the code landed — has no repo target to fix, so it is closed
  `accepted-wont-fix` with a stated reason and not filed. The outcome is keyed on the finding's **target**, not the
  producing node: a `plan-findings` id anchored at a real repo file files exactly like the bullet above.
- an id with severity `folded` is closed by construction and carried into the fold table as `folded` — never open, never
  filed.
- an unmatched `blocking` id should not occur — one does not survive into the newest asset without a bounce that
  resolved it. If one somehow does, it is treated like an open should-fix id: filed, with the fold table saying it was
  found still blocking.

A disposition citing an id absent from the newest asset of its kind is stale — its round was superseded, most often
because the finding no longer reproduces — and is ignored.

The fold table — id, source (`plan-review` / `review`), severity, anchor, disposition, and reference (commit hash /
issue URL / reason) — lands in the retrospective node's own `retrospective` asset.
