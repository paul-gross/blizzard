# Spend

An unattended fleet spends against the operator's harness billing with no ceiling by default; two optional caps live in
a `[cost]` table in `blizzard-runner.toml`, absent by default — no table, no cap. Every billed cost blizzard records
derives from the harness's own reported figure, by subtraction and never by pricing: blizzard keeps no pricing table of
its own. Where a step's own reported figure is a subscription's literal zero, the runner may still read OpenCode's own
cached rates and label the result as an estimate ([Estimated cost](#estimated-cost) below) — never fabricated, never
guessed past what that cache says, and never presented as billed spend.

## The two readings of a reported figure

A harness is free to charge its figure against the whole session rather than the invocation that produced it. Claude
Code does exactly this on a resume, so its figure already contains every earlier turn of that session — and because a
session outlives the worker process running it, the figure reaches back across every worker the session was resumed
into. Each recorded fact therefore carries only its own share: the harness's figure minus everything the session had
already banked. Where an earlier invocation of that session recorded no cost at all, its dollars are not among what was
banked, so the next fact absorbs them along with its own: the session's total still lands right, while that one fact
reads high.

Which reading applies is settled per envelope, from the token count the harness says its figure covers, and never from a
version number. A version table would encode today's knowledge of a harness that updates itself underneath the runner,
and go stale silently — as wrong dollars, which is the one failure mode nothing else on the board would reveal. The
reading is whichever of two candidate counts the reported one sits nearer: the invocation's own tokens, or those plus
everything the session has banked. The candidates are separated by exactly what the session has banked, so the choice is
only as sharp as that separation is wide — and on a session's first invocation, with nothing banked, they coincide and
no decision is needed. An envelope reporting no such count is charged verbatim, its figure being that invocation's by
construction; one whose session total has gone meaningfully backwards — further than a rounding step — records no cost
at all, landing as PARTIAL rather than as a fabricated zero.

One shape cannot be separated: an invocation-scoped figure covering work the envelope's own token counts leave out — a
harness billing subagent turns it does not report. Because the reading is nearest-of-two, that hidden work only has to
reach **half** of everything the session has banked for the figure to be read as session-scoped; the subtraction that
follows then charges the invocation short, or, where it runs backwards, records no cost for it at all. Claude Code's
scope count does reach model work its per-invocation counts leave out, but its figure is session-scoped wherever that
happens, so the reading still lands right; an adapter that was invocation-scoped and billed uncounted work would not.

Nothing of this is visible while it goes right: a chunk's cost column and the fleet total simply read what the fleet
spent. A rejected figure shows as the PARTIAL tilde, and the runner log carries one line naming the reported figure that
read below its session — the one place the two causes of an absent cost separate, a crashed worker being the other. A
session already running when a runner upgrades onto this reading keeps whatever its earlier facts banked: every
invocation from the upgrade forward is charged its own share, while the dollars those earlier rows recorded stay as they
were recorded, so that one session's lifetime total can read high until it ends.

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

## Usage-limit pause

A harness can hit its own subscription's usage limit mid-turn, independently of either cap above — Claude Code and
OpenCode each report this in a recognizable shape (a synthetic rate-limit transcript record, an OpenCode 429 `error`
event), never inferred from cost or token figures. The runner classifies an exited worker generation or judge
elicitation against that shape and, on a match, engages its own local pause brake — the same brake the ceiling and
`runner pause` set — with a reason naming the harness and, where the harness reported one, its reset time; where it did
not, the reason falls back to the soonest reset among this runner's own declared subscriptions' latest sampled windows
already past 100% utilization ([External subscription usage](#external-subscription-usage) below), or carries no reset
time at all. The limited lease is not failed and consumes no retry: it is parked in place, exactly like a per-chunk
pause leaves a chunk, and resumes automatically once the brake lifts. As with the ceiling, only an explicit operator act
clears it — `blizzard runner start`, or the runner panel's Resume — never an elapsed reset time the runner read out of
the harness's own report.

## Partial totals

When a worker dies before the harness emits its final usage envelope, the attempt's tokens are recorded from the
transcript but its cost is genuinely unknown: an absent-cost row contributes its tokens and zero billed dollars. A row
flags its total PARTIAL (a tilde on the board and in `hub status`) only when it carries **neither** a billed cost nor a
reported estimate — see [Estimated cost](#estimated-cost) below; a row carrying only an estimate is not a lower bound
and does not flag PARTIAL. The runner's own two caps are a separate reading: both trip on the billed lower bound
alone, never on an estimate, and each surfaces its own PARTIAL on its own carrier — the escalation, or the recorded
pause reason — whenever some invocation it summed carried no billed cost. That PARTIAL means exactly "no billed cost
recorded", unchanged by whether the same invocation also carries an estimate, so a crash-heavy chunk never silently
reads cheap. A crash is not the only way a row lands cost-absent: a reported figure that runs backwards against what its
session already banked records no cost either, for the reason
[The two readings of a reported figure](#the-two-readings-of-a-reported-figure) gives. A graceful restart no longer
produces a PARTIAL row on its own: the shutdown drain (see [Graceful restart](./recovery.md#graceful-restart)) waits out
each marked worker's own SIGINT-triggered envelope, so only a worker SIGKILLed at the drain's deadline, one that exits
on SIGINT without writing an envelope, or an outright crash still lands cost-absent.

`blizzard hub status` shows the per-chunk cost column, the fleet total, and a paused runner's ceiling reason;
[Estimated cost](#estimated-cost) below owns which surfaces add the estimate beside the billed figure.

## Estimated cost

An OpenCode step billed against a subscription reports its cost as a literal zero, which the runner records as no
billed cost: when every step of an invocation reads that way, its `cost_usd` stays `None`, exactly like any other
cost-absent row. When that step's model has a priced entry in OpenCode's own local cache — `models.json`, refreshed by
OpenCode itself, keyed by provider and then by model — the runner estimates that one step's dollar cost from the cache
instead, pricing it the way OpenCode itself prices a step. Because OpenCode's rate can depend on a step's own prompt
size, the rate is always chosen from that one step's own tokens, never from an invocation's summed total, so two steps
of the same invocation can price at two different rates. A step whose model has no entry in the cache, or whose cache
file is missing or unreadable, stays cost-absent: never a raise, never a guess past what the cache says.

The runner reads the cache from `XDG_CACHE_HOME/opencode/models.json` when `[worker] env_passthrough` passes
`XDG_CACHE_HOME` through to the worker, or from `HOME/.cache/opencode/models.json` otherwise — the default OpenCode
itself writes to. This is the same worker-owned environment mapping the worker's own identity is built from, never a
constant path, so relocating a fleet's OpenCode cache is a passthrough change, not a runner one. A worker running on
OpenCode's own configured default model — with no `provider/model` the runner ever resolved for it — still gets an
estimate: the process's own stdout events never name a step's provider or model, but when the lease's model is
unpinned and transcripts are wired, the runner reads that invocation's own transcript export, whose lines carry each
step's `providerID`/`modelID` regardless, and prices from the model it observes there. A pinned lease never pays for
that export read, since its own stamp already names what ran; with transcripts unwired, or an export naming no model
either, the step stays unestimated.

For an invocation that ends with a usage envelope, a billed step and a zero-cost, estimated step can both appear
together: its billed steps sum into `cost_usd`, and its estimated steps separately sum into `estimated_cost_usd` — the
two are never merged and never double counted. The transcript fallback (the crash/reap path) never bills — it always
records `cost_usd` as absent — and drops any estimate it would otherwise have built if it saw even one step with a
real cost, so that row still reads cost-absent and stays PARTIAL rather than surfacing a partial estimate. Separately
from all of that, the hub accepts and stores a reported `estimated_cost_usd` for a row: its own column, summed into
its own total, never folded into `cost_usd`. A chunk's or the fleet's billed cost is never inflated by an estimate,
and an estimate is never presented as billed spend.

The estimate renders labeled `$X.XX est.`, only where a total carries one: on `blizzard hub chunk show` on its own
line; beside the billed figure in `hub status`'s per-chunk column and fleet total, in `hub chunk list`, and in
`hub analytics summary`'s spend rows, so a chunk whose cost is entirely estimated reads `$0.00  $X.XX est.` there
rather than a bare `$0.00`; and on the board: chunk cards, the board header's spend figure, the chunk detail dock and
its timeline, the mobile glance board, and the runner panel's chunk detail.

An estimate never feeds either cap. `runner_ceiling_usd` and `chunk_cap_usd` ([The two caps](#the-two-caps) above) are
both checked against billed cost alone: a chunk or a runner can run up real, uncapped subscription spend while every
one of its steps shows only an estimate, and the caps stay blind to it — an operator relying on either cap to bound
subscription spend needs to watch the estimate figure itself.

## External subscription usage

A harness on a metered subscription tracks its own account's rate-limit window utilization independently of anything
blizzard spends or caps; the runner samples it on the cadence `[external_subscription_usage]` `sample_interval_seconds`
in `blizzard-runner.toml` sets (default 300 seconds when the table or key is absent), or the cadence a
`[[subscription]]` declaration sets when a runner declares its subscriptions explicitly — a runner with none declared
runs the single legacy table's own subscription unchanged. The sampled utilization is advisory only — it never throttles
claiming, scheduling, or spawning, and no cost cap consults it.

Every sample crosses the wire carrying the `slug` of the subscription it belongs to, and the hub rejects one that does
not. A runner predating per-subscription reporting therefore needs upgrading before the hub that drops the singular
usage response — [`docs/upgrade.md`](../upgrade.md) owns that ordering.

Two providers ship a sampler binding, selected by a declaration's `provider` key: `anthropic`, reading Claude Code's
OAuth plan, and `openai`, reading a ChatGPT plan's Codex usage. A provider naming neither stays declared and unsampled
rather than failing configuration, and no sample renders as an absent usage block on the board — never a fabricated
zero. The hub tracks every declared subscription's own sample independently, keyed on slug: one subscription going stale
or unsampled never blanks a sibling's. The runner panel renders a paced-window bar per sampled window, only when the
runner has a non-stale sample to show. Both plans meter a 5h and a 7d window today, but only `anthropic` reads those two
as fixed: `openai` labels each window from the length its own response reports, so a plan metering differently is
rendered as it comes rather than forced into that pair.

Each binding reads the credential file its own vendor CLI writes: `~/.claude/.credentials.json` for `anthropic`,
`~/.codex/auth.json` for `openai`, either overridable per declaration with `credentials_path`. **Blizzard never writes
that file** — the vendor CLI owns the refresh flow, its lock, and its refresh-token rotation — but renewal is delegated
to it, per provider. `openai` carries a renewal binding: once the access token is within ten minutes of its own expiry,
the runner asks `codex app-server` for a proactive refresh on the subscription's own sampling cadence, before that
cadence's sample, so an idle runner keeps sampling without anyone running `codex` by hand. It needs the `codex` binary
reachable from the runner's environment; a renewal that times out or is refused is recorded as a failed outcome, never a
raise, and the sample still follows it. `anthropic` carries none: a fleet's own Claude Code workers renew that token
continuously, and an idle Anthropic runner whose token lapses shows the lapsed condition below until its next worker
refreshes the file.

A sample that produces nothing carries one of four reasons: `credential_lapsed` (a token past its own expiry, or a 401
from the provider), `credential_unreadable` (a missing or unparseable credential file), `endpoint_unreachable` (a
connection failure, a timeout, or any other non-2xx status), and `response_unparseable` (a body the binding could not
read). Every reason surfaces on the runner: `blizzard runner status`, the runner panel's subscriptions rail, and
`GET /api/subscriptions` show the newest attempt's outcome, its miss reason, and its renewal outcome, and the probe
below prints the reason in operator words. Every miss crosses to the hub the same way a sample does — reporting
`{slug, name, missed_at, reason}`, never a token, a refresh token, or a path — but only `credential_lapsed` renders: a
slug whose newest lapsed miss postdates its newest sample shows on the board as "credential lapsed — log in again on
this runner" in place of its pace bars, ageing out under the same staleness gate a sample does. Any other reason leaves
the board exactly as an unsampled slug leaves it today, even though the hub has stored it.

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
names no binding — a typo, most often — prints that it has no sampler, where a declared-and-bound subscription that got
nothing back prints its miss reason instead — `credential lapsed: log in again` for a token past its expiry. A runner
with no `[[subscription]]` declared has exactly one slug to name: `anthropic`, the legacy table's own subscription.
