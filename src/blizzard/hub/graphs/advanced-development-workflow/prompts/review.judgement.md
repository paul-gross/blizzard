# Review — judgement

Render your review verdict. The `review-findings` asset must be published before it — what you reviewed per axis, every
finding, and how you adjudicated every entry in `review-finding-refutes`.

A finding whose refutation you accepted is resolved, exactly as if it had been fixed — it does not block `pass`.

Select `pass` if the work meets the plan and the item's intent with no blocking issue on any axis. Select `fail` if any
blocking issue remains; your findings ride back into the build node.

Alongside your verdict, submit this node's retrospective: run `blizzard runner artifact create --name retrospective`
with a few honest lines on stdin — what went well, what didn't, and what the next node or run should know.

## The review-finding-delta asset

Before your verdict, on every round, also run `blizzard runner artifact create --name review-finding-delta`, content
on stdin: a JSON object `{"entries": [...]}`, one entry per finding you have adjudicated this round — `fixed` or
`refuted`, plus, only on a `pass`, `deferred` for a still-open should-fix. Leave out a blocking finding still open on a
`fail`: it is why the round failed, not something this delta records. Each entry carries `ref` and `disposition`
(`deferred`, `fixed`, or `refuted`). A `deferred` entry additionally carries `severity`, `scope` — run
`blizzard runner scope list` first and reuse a listed slug where one already fits, rather than inventing one — `class`,
`locus`, and `summary`; `fixed`/`refuted` carry no more than `ref` and `disposition`. A `deferred` entry can never
carry `severity: blocking` — that cannot coexist with `pass`. Read the full shape live with `blizzard runner artifact
get --scope system review/finding-format --content` and follow it exactly; on failure or an empty read, use the
restatement above.
