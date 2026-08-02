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
- **severity** — exactly `blocking` or `should-fix`. (`plan-review.md` calls its blocking tier "must-fix" in
  prose — same value, `blocking`, in the entry.)
- **anchor** — `<repo>/<path>:<line>` or `<repo>/<path>::<symbol>`. The repo prefix matters once a chunk spans
  more than one repo; a bare `file:line` is ambiguous there.
- a one- or two-sentence description, specific and actionable — what's wrong, not just where.

Example, inside a `review-findings` asset:

```
F1 — should-fix — blizzard/src/blizzard/hub/runner.py:142
  Retry counter isn't reset after a successful heartbeat, so a flaky-then-recovered
  worker still escalates on its next transient failure.
```

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

Disposing every `blocking` finding is already required to clear the bounce (unchanged). Disposing a
`should-fix` finding is optional — fix it if the fix is cheap, otherwise leave it undisposed. An undisposed
should-fix finding is not an error to raise here; it's retrospective's to catch.

## The retrospective fold

`retrospective.md` enumerates every id in the **newest** `plan-findings` asset and the **newest**
`review-findings` asset — the series' latest submission of each; an earlier round is superseded and out of
the fold entirely, whether or not its findings were ever individually disposed. It matches each id against
disposition records recorded anywhere in the chunk's node `retrospective` assets, then:

- a matched id is closed — carry its disposition into the fold table.
- an unmatched `should-fix` id is open — file a forge issue for it, following the workspace's own
  issue-filing convention if it declares one (skill, format, label set), otherwise a plain
  `gh issue create` run from inside that finding's own repo worktree so `gh` targets the right forge —
  the anchor's repo segment names it — and record that filing as its disposition (`filed-as-issue`, with
  the created issue's URL). The filing *is* the disposition; nothing further closes it.
- an unmatched `blocking` id should not occur — a blocking finding does not survive into the newest asset
  without a bounce that resolved it. If one somehow does, treat it exactly like an open should-fix id: file it,
  and say in the fold table that it was found still blocking.

A disposition citing an id absent from the newest asset of its kind is stale — the round it answered was
superseded, most often because the finding no longer reproduces. Ignore it; do not chase it forward.

Retrospective includes the resulting fold table — id, source (`plan-review` / `review`), severity, anchor,
disposition, and reference (commit hash / issue URL / reason) — in its own `retrospective` asset.
