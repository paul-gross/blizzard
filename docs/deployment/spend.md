# Bounding spend and reading rate-limit windows

## Bounding fleet spend — cost caps and the spend kill-switch

An unattended overnight fleet spends against the operator's harness billing with no ceiling by default. Two optional
caps bound it, both configured in a `[cost]` table in `blizzard-runner.toml` and both **absent by default — no `[cost]`
table means no cap and no ceiling, exactly the prior behavior**. Cost figures are the harness's own `total_cost_usd`;
blizzard maintains no pricing table and never fabricates a cost.

```toml
[cost]
# Per-chunk spend cap. When a chunk's total cost crosses this, it parks needs_human
# at its next step boundary. Absent = no per-chunk cap.
chunk_cap_usd = 5.0

# Runner spend ceiling over a rolling window. When this runner's spend across the
# trailing window crosses this, the local pause brake engages. Absent = no ceiling.
runner_ceiling_usd = 50.0

# The rolling window the ceiling sums over, in hours. Defaults to 24.0; only
# consulted when runner_ceiling_usd is set.
window_hours = 24.0
```

- **Per-chunk cap (`chunk_cap_usd`).** Checked **between attempts**, never by killing a live worker: when a chunk's
  derived total cost reaches the cap, the runner parks it `needs_human` at the next step boundary with an escalation
  naming the cap and the spend, and the usual takeover command to resume. A capped chunk is not a failed one — no retry
  is consumed. Resuming is a human decision: raise or clear the cap, then requeue the chunk and it proceeds.
- **Runner ceiling (`runner_ceiling_usd`, `window_hours`).** Checked at each tick: when this runner's spend over the
  trailing `window_hours` crosses the ceiling, the runner's **local pause brake** engages (the same brake `runner pause`
  sets — every spawn site suppressed, no retries consumed, live workers left to finish), carrying the ceiling and the
  spend as the pause's own recorded reason. Unlike the per-chunk cap, it raises no escalation: the ceiling is
  runner-scoped, so there is no one chunk to park. The window is a rolling last-N-hours sum; **it does not
  auto-unpause** when the window later rolls the spend back under the ceiling. Clearing the brake is always an explicit
  operator decision, never automatic — `blizzard runner start` at the CLI, or the runner panel's Resume control, exactly
  as for a hand-issued pause. `GET /api/runners` and `blizzard hub status` surface the ceiling reason on the paused
  runner, so it reads differently from a manual pause.
- **Cost-absent rows are a conservative lower bound.** When a worker crashes or is `kill -9`ed before the harness emits
  its final usage envelope, blizzard records the attempt's tokens from the session transcript but its **cost is
  genuinely unknown** — so an absent-cost row contributes its tokens but **$0** to the cost sum, making the total a
  lower bound, flagged **PARTIAL** wherever it is shown (a `~` marker on the board and in `hub status`). Both caps trip
  on this lower bound and surface PARTIAL, each on its own carrier — the per-chunk cap in the escalation it raises, the
  runner ceiling in the reason recorded on the pause — so an operator knows the true spend may be higher, and a cap
  never silently under-counts a crash-heavy chunk into looking cheap.

See `blizzard hub status` for the per-chunk cost column, the fleet total, and a paused runner's ceiling reason; the
board's chunk cards and detail dock show the same figures live.

## Surfacing subscription rate-limit windows — an advisory-only board read

A harness that runs under a metered subscription plan — Claude Code's OAuth-backed plan is the first — tracks its own
account's rate-limit window utilization independently of anything blizzard spends or caps. The runner can sample that
figure on a configurable cadence, controlled by an `[external_subscription_usage]` table in `blizzard-runner.toml`,
**absent by default — no table means the default cadence below, exactly the prior behavior**.

```toml
[external_subscription_usage]
# How often the runner samples Claude Code's own OAuth usage endpoint, in seconds.
# Defaults to 300 (5 minutes) when the table (or just this key) is absent.
sample_interval_seconds = 300
```

- **Claude-Code-only, today.** Claude Code is the only harness adapter that ships, and the only one with a subscription
  concept to sample. The seam's contract already covers a harness that has none — it reports no sample rather than a
  figure — and a runner with no sample renders as the usage block simply being absent on the board, never a fabricated
  zero or empty reading.
- **Advisory only.** The sampled utilization never throttles or backpressures claiming, scheduling, or spawning in any
  way — it is a read for a human, not an input to the runner's own decisions. Nothing about cost caps, the spend
  kill-switch, or work claiming changes based on it.
- **Credentials never leave the runner machine.** The sample step reads the runner's own local OAuth credential file to
  authenticate the usage request; only the derived utilization percentages, window labels, and reset times cross the
  wire to the hub — the bearer token itself is never reported, stored, or forwarded.
- **`blizzard runner external-usage probe`** is a read-only diagnostic that samples once and prints the parsed snapshot
  without writing to the store, ticking, or reporting anything to the hub — useful for confirming the runner's
  credentials and cadence are working before waiting on the next scheduled sample.

See the runner panel on the board for a paced-window bar per sampled window (`5h`/`7d` for Claude Code), rendered only
when a runner has a non-stale sample to show.
