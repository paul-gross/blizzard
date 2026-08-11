# The findings docket

The shared format for every finding this graph records and every disposition that closes one — the single
owner `review.md`, `plan-review.md`, `build.from-review.md`, `plan.from-plan-review.md`, and `retrospective.md`
all point at instead of restating. Built on the chunk's artifact series
(`blizzard-context:/domain/artifacts.md` — append-only, per node and name, reads resolve to the newest entry):
a docket entry lives inside a `plan-findings` or `review-findings` asset; a disposition lives inside the
responding node's own `retrospective` asset (every node already produces one).

## Docket entry

Every finding recorded in a `plan-findings` or `review-findings` asset carries:

- **id** — stable *within this asset submission*: `F1`, `F2`, … A fresh cold-eyes pass after a bounce is a new
  submission, so it restarts at `F1`; it does not continue a prior round's numbering.
- **severity** — `blocking` or `should-fix`; a `plan-findings` entry may instead carry `folded`, an
  improvement-tier finding the gate already fixed in the `reviewed-plan` it published under an
  `acceptable` verdict. Recording `folded` is itself the closure — no node ever owes it a disposition.
  On a `must-fix` verdict no fold survives the verbatim republish, so an improvement-tier finding is
  recorded `should-fix` there, never `folded`. (`plan-review.md` calls its blocking tier "must-fix" in
  prose — same value, `blocking`, in the entry.)
- **anchor** — `<repo>/<path>:<line>` or `<repo>/<path>::<symbol>`. The repo prefix matters once a chunk spans
  more than one repo; a bare `file:line` is ambiguous there. A finding whose target is a chunk asset rather
  than a repo file — the plan-apparatus case — anchors as `<asset-name>::<section>`, e.g.
  `plan::Acceptance criteria`.
- a one- or two-sentence description, specific and actionable — what's wrong, not just where.

Example, inside a `review-findings` asset:

```
F1 — should-fix — blizzard/src/blizzard/hub/runner.py:142
  Retry counter isn't reset after a successful heartbeat, so a flaky-then-recovered
  worker still escalates on its next transient failure.
```

## Refutation record

A finding can be answered two ways: **fixed**, or **refuted**. A refutation says the finding should not be
acted on at all — it is factually wrong, rests on a false premise, or demands work the change's scale does
not warrant. It is not "I would rather not."

The node responding to a docket records its refutations in a dedicated asset, submitted alongside its
usual work: `plan` submits `plan-finding-refutes`, `build` submits `review-finding-refutes`. Each entry:

- **anchor** — the finding's `<repo>/<path>:<line>` or `<repo>/<path>::<symbol>`, copied verbatim.
- **cited id** — `<node>:<id>` from the round being answered, e.g. `review:F2`.
- **the argument** — why the finding is wrong, with evidence: the code, the command, the fact it missed.

**The anchor is what matches, not the id.** Ids are stable only within one asset submission, and a fresh
cold pass restarts at `F1` — so a refutation carrying only an id cannot be matched against the next
round's renumbering. The id records which round was answered; the anchor is what the reviewer keys on.

**The newest submission is the entire record.** Reads resolve to the newest entry per node and name, so a
later submission does not add to the earlier one — it replaces it. Every submission therefore restates
**every refutation still standing**, including ones a gate already accepted in an earlier round, each
marked with the round it was first raised in and its outcome so far (`open` or `accepted`). Drop an entry
only when the finding it answers is genuinely dead: fixed, or withdrawn by the gate.

The gate reads **only** the newest submission and never goes looking for an older one — an older epoch is
shadowed by design and carries no standing. It must resolve every entry explicitly: accept it and not
re-raise, or reject it and re-raise with an answer to the argument. An entry already marked `accepted`
stays accepted — do not re-adjudicate it. Silence is not acceptance: an unanswered refutation is still an
open finding. Refuting is a claim to be adjudicated, never a veto, and a refutation the gate accepts still
needs a disposition below (`accepted-wont-fix`).

Submit the asset on every completion, even when nothing was refuted — one line saying so. An explicit
"nothing to refute" tells the gate the channel was considered; an absent asset is ambiguous. Take care
that a round which fixed everything does not submit a bare "nothing to refute" while earlier refutations
are still standing: that submission would drop them, and the finding would return on the next cold pass.

A refutes asset that carries no recognizable entries — in practice the judgement assessment submitted by
the completion fallback when the worker declared nothing — is read as "nothing refuted". The gate records
that reading and proceeds; it is not an error to adjudicate.

There is deliberately **no refutation channel for verification**. A failed verification method is a
mechanical fact, not a judgement to argue with; the answer is to fix the change or fix the method.

## Disposition record

A node re-entering to address a docket — `build` from `build.from-review.md`, `plan` from
`plan.from-plan-review.md` — records one disposition per finding it addresses, inside **its own**
`retrospective` asset (already required at judgement time; add a disposition list alongside the usual few
lines). Each disposition:

- cites the finding as `<node>:<id>` — the producing node's name plus the id from the asset attached to
  *this* node-step's envelope, e.g. `review:F1`.
- is exactly one of:
  - `fixed-in-chunk` — plus the commit hash that fixed it.
  - `filed-as-issue` — plus the issue URL.
  - `accepted-wont-fix` — plus a one-line reason.

A `folded` finding is the one exception: the gate that recorded it already fixed it in the `reviewed-plan`
it published, so no node records a disposition for it — the fold table carries `folded` in its disposition
column, closed by construction.

Disposing every `blocking` finding is already required to clear the bounce (unchanged). Disposing a
`should-fix` finding is optional — fix it if the fix is cheap, otherwise leave it undisposed. But:
a superseded round's undisposed findings are abandoned by design (see below), so leaving one undisposed
loses it if this round is later superseded by a fresh submission. If it matters beyond this chunk,
dispose it now — `filed-as-issue` or `accepted-wont-fix` — rather than leaving it to a fold that will not
see it once superseded.

## The retrospective fold

**Decision: supersession is authoritative.** `retrospective.md` enumerates every id in the **newest**
`plan-findings` asset and the **newest** `review-findings` asset — the series' latest submission of each;
an earlier round is superseded and out of the fold entirely, whether or not its findings were ever
individually disposed. A superseded round's undisposed findings are abandoned by design — this is
deliberate: the next review is a full cold pass over the change **as it stands** (`review.md`), not a
delta over what changed since the last round, so a defect still present in the code is re-reported under
a new id in the newest asset, where the fold already sees it. When a chunk had a superseded round, the
fold table names it, so a reader sees the supersession rather than a silent absence. It matches each id
against disposition records recorded anywhere in the chunk's node `retrospective` assets, then:

- a matched id is closed — carry its disposition into the fold table.
- an unmatched `should-fix` id whose target is a real repo file, describing a defect still present in
  the change, is open — file a forge issue for it, following the workspace's own issue-filing
  convention if it declares one (skill, format, label set), otherwise a plain `gh issue create` run
  from inside that finding's own repo worktree so `gh` targets the right forge — the anchor's repo
  segment names it — and record that filing as its disposition (`filed-as-issue`, with the created
  issue's URL). The filing *is* the disposition; nothing further closes it.
- an unmatched `should-fix` id whose target is an **immutable artifact** has no repo target the bullet
  above could point a fix at, so it is closed `accepted-wont-fix` with a stated reason instead, and not
  filed. In practice this is a plan-apparatus finding against the consumed plan asset — an acceptance
  criterion's wording, a guard command's pattern, a self-consistency inventory — the plan is immutable
  and was already consumed before the code landed. `plan-findings` ids are otherwise in the fold's scope
  like any other id: the outcome above is keyed on the finding's **target**, not on which node produced
  it, so a `plan-findings` id anchored at a real repo file and describing a defect still present in the
  change files exactly like the bullet above.
- an id with severity `folded` is closed by construction — the gate already fixed it in the `reviewed-plan`
  it published. Carry it into the fold table with `folded` in the disposition column; it is never open and
  never filed.
- an unmatched `blocking` id should not occur — a blocking finding does not survive into the newest asset
  without a bounce that resolved it. If one somehow does, treat it exactly like an open should-fix id: file it,
  and say in the fold table that it was found still blocking.

A disposition citing an id absent from the newest asset of its kind is stale — the round it answered was
superseded, most often because the finding no longer reproduces. Ignore it; do not chase it forward.

Retrospective includes the resulting fold table — id, source (`plan-review` / `review`), severity, anchor,
disposition, and reference (commit hash / issue URL / reason) — in its own `retrospective` asset.
