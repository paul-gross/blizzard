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
in `blizzard-runner.toml` sets (default 300 seconds when the table or key is absent), or the cadence a
`[[subscription]]` declaration sets when a runner declares its subscriptions explicitly — a runner with none declared
runs the single legacy table's own subscription unchanged. The sampled utilization is advisory only — it never throttles
claiming, scheduling, or spawning, and no cost cap consults it.

Two providers ship a sampler binding, selected by a declaration's `provider` key: `anthropic`, reading Claude Code's
OAuth plan, and `openai`, reading a ChatGPT plan's Codex usage. A provider naming neither stays declared and unsampled
rather than failing configuration, and no sample renders as an absent usage block on the board — never a fabricated
zero. The hub tracks every declared subscription's own sample independently, keyed on slug: one subscription going stale
or unsampled never blanks a sibling's. The runner panel renders a paced-window bar per sampled window, only when the
runner has a non-stale sample to show. Both plans meter a 5h and a 7d window today, but only `anthropic` reads those two
as fixed: `openai` labels each window from the length its own response reports, so a plan metering differently is
rendered as it comes rather than forced into that pair.

Each binding reads the credential file its own vendor CLI writes: `~/.claude/.credentials.json` for `anthropic`,
`~/.codex/auth.json` for `openai`, either overridable per declaration with `credentials_path`. **Neither binding
refreshes the credential it reads** — the vendor CLI owns that flow and its lock. That is invisible for `anthropic`,
whose token a fleet's own Claude Code workers renew continuously, and load-bearing for `openai`, whose token expires
about ten days after the last `codex` run: on a runner that never runs `codex`, the subscription samples until that
lapses and reports nothing after. Running any `codex` command refreshes it.

Credentials never leave the runner machine: the sample reads the runner's own local OAuth credential file, and only
derived utilization percentages, window labels, and reset times cross the wire to the hub — the bearer token is never
reported, stored, or forwarded.

Declaring any `[[subscription]]` turns the legacy table off entirely, so a runner adding a second plan must declare both
— and keep the Anthropic one on the slug `anthropic`, the join key its existing hub-side samples are stored under. "Off
entirely" includes the legacy table's own `credentials_path` and `sample_interval_seconds`: a runner that had customized
either must restate it on the Anthropic declaration, or it silently reverts to the default path and 300 seconds.

```toml
[[subscription]]
slug = "anthropic"          # keep this slug: renaming it orphans the stored history
name = "Anthropic"
provider = "anthropic"

[[subscription]]
slug = "openai"
name = "OpenAI"
provider = "openai"
sample_interval_seconds = 300
```

`blizzard runner external-usage probe <slug>` samples one declared subscription, by its slug, once and prints the parsed
snapshot without writing, ticking, or reporting to the hub — confirming that subscription's credentials and cadence
without waiting on a scheduled sample. It is also where the two silent outcomes separate: a declaration whose `provider`
names no binding — a typo, most often — prints that it has no sampler, where a declared-and-bound subscription that
simply got nothing back reports no sample instead. A runner with no `[[subscription]]` declared has exactly one slug to
name: `anthropic`, the legacy table's own subscription.
