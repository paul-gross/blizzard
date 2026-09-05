# Spend

An unattended fleet spends against the operator's harness billing with no ceiling by default; two optional caps live in
a `[cost]` table in `blizzard-runner.toml`, absent by default — no table, no cap. Cost figures are the harness's own
`total_cost_usd`; blizzard maintains no pricing table and never fabricates a cost.

## The two caps

`runner_ceiling_usd`, summed over a rolling window of `window_hours` (default 24, consulted only when the ceiling is
set), is checked each tick: crossing it engages the runner's local pause brake — the same brake `runner pause` sets:
spawn sites suppressed, no retries consumed, live workers left to finish — recording the ceiling and spend as the
pause's reason. The ceiling raises no escalation — runner-scoped, it has no one chunk to park. It does not auto-unpause
when the rolling window later drops the spend back under it: clearing the brake is always an explicit operator act —
`blizzard runner start`, or the runner panel's Resume — exactly as for a hand-issued pause. `GET /api/runners` and
`blizzard hub status` surface the ceiling reason on a paused runner, so it reads differently from a manual pause.

`chunk_cap_usd` is checked between attempts, never by killing a live worker: when a chunk's total cost reaches it, the
runner parks the chunk `needs_human` at the next step boundary with an escalation naming the cap, the spend, and the
usual takeover command. A capped chunk is not failed — no retry is consumed; resuming is human: raise or clear the cap,
then requeue.

## Partial totals

When a worker dies before the harness emits its final usage envelope, the attempt's tokens are recorded from the
transcript but its cost is genuinely unknown: an absent-cost row contributes its tokens and zero dollars, so every total
is a lower bound flagged PARTIAL (a tilde on the board and in `hub status`). Both caps trip on that lower bound and
surface PARTIAL on their own carrier — the escalation, or the recorded pause reason — so a crash-heavy chunk never
silently reads cheap.

`blizzard hub status` shows the per-chunk cost column, the fleet total, and a paused runner's ceiling reason; the
board's chunk cards and detail dock show the same figures live.

## External subscription usage

A harness on a metered subscription tracks its own account's rate-limit window utilization independently of anything
blizzard spends or caps; the runner samples it on the cadence `[external_subscription_usage]` `sample_interval_seconds`
in `blizzard-runner.toml` sets (default 300 seconds when the table or key is absent), or the cadence a `[[subscription]]`
declaration sets when a runner declares its subscriptions explicitly — a runner with none declared runs the single
legacy table's own subscription unchanged. The sampled utilization is advisory only — it never throttles claiming,
scheduling, or spawning, and no cost cap consults it.

Claude Code's OAuth plan is the only subscription concept a shipping adapter has; a harness with none reports no sample,
and no sample renders as an absent usage block on the board — never a fabricated zero. The runner panel renders a
paced-window bar per sampled window (5h and 7d for Claude Code), only when the runner has a non-stale sample to show.

Credentials never leave the runner machine: the sample reads the runner's own local OAuth credential file, and only
derived utilization percentages, window labels, and reset times cross the wire to the hub — the bearer token is never
reported, stored, or forwarded.

`blizzard runner external-usage probe <slug>` samples one declared subscription, by its slug, once and prints the
parsed snapshot without writing, ticking, or reporting to the hub — confirming that subscription's credentials and
cadence without waiting on a scheduled sample. A runner with no `[[subscription]]` declared has exactly one slug to
name: `anthropic`, the legacy table's own subscription.
