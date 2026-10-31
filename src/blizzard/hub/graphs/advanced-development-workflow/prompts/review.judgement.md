# Review — judgement

Render your review verdict. The `review-findings` asset must be published before it — the tip you judged per repo, this
round's scope, what you reviewed per axis and the path of each criteria document read for it, every finding with its
severity (and, on a should-fix, its `in-diff` / `outside-diff` tag), and how you adjudicated every entry in
`review-finding-refutes`.

A finding whose refutation you accepted is resolved, exactly as if it had been fixed — it does not block `pass`.

## What blocks depends on the round

A `blocking` finding blocks on every round. A `should-fix` blocks only on a first round — no `review-findings` asset
from an earlier round — and only when tagged `in-diff`: a defect in what this change itself wrote is repaired while the
build still holds the context. On a re-visit — any round with one, re-entry after `pre-push` included — no should-fix
blocks, a still-open round-one `in-diff` one included: it is deferred on `pass`, so the loop converges after one repair
round. Severity describes the defect and the round decides whether a should-fix blocks — never re-grade or re-tag a
finding to steer the verdict.

Select `pass` if the work meets the plan and the item's intent and nothing blocks on any axis — on a re-visit, judged
over the delta and the prior round's findings it resolved. Select `fail` if anything blocks; your findings ride back
into the build node.

Alongside your verdict, submit this node's retrospective: run `blizzard runner artifact create --name retrospective`
with a few honest lines on stdin — what went well, what didn't, and what the next node or run should know.

## The review-finding-delta asset

Before your verdict, on every round, also run `blizzard runner artifact create --name review-finding-delta`, content on
stdin: a JSON object `{"entries": [...]}`, one entry per finding you have adjudicated this round — `fixed` or `refuted`,
plus, only on a `pass`, `deferred` for a still-open should-fix. On a `fail`, leave out a still-open finding that blocks
this round — a `blocking` one, or a round-one `in-diff` should-fix: it is why the round failed, not something this delta
records. Each entry carries `ref` and `disposition` (`deferred`, `fixed`, or `refuted`). A `deferred` entry additionally
carries `severity`, `scope` — run `blizzard runner scope list` first and reuse a listed slug where one already fits,
rather than inventing one — `class`, `locus`, and `summary`; `fixed`/`refuted` carry no more than `ref` and
`disposition`. A `deferred` entry can never carry `severity: blocking` — that cannot coexist with `pass`. Read the full
shape live and follow it exactly: `blizzard runner artifact get --scope system review/finding-format --content`; on
failure or an empty read, use the restatement above.
