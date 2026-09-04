# Findings and garden proposals

A **finding** is one observation a routine's run recorded; a **garden proposal** is a proposed response to one or more
findings. Both are hub-stored records, and both now carry closing verbs of their own.
[`domain/findings-and-proposals.md`](https://github.com/paul-gross/blizzard-context/blob/master/domain/findings-and-proposals.md)
owns the concept; this covers the verbs only.

## Findings

`blizzard hub finding list --routine <name> --scope <slug> [--include-gone]` and `show <finding_id>` are the operator's
own finding reads — an operator or an integration holding a hub credential, naming any routine and any scope, live only
unless `--include-gone`, which also surfaces every exited finding alongside a merely `gone` one. `state`, `live`,
`last_seen_at`, and `observed_count` track each finding's own fact history, changing as later runs observe or lose it,
or a person exits or reopens it.

A running pass cross-references its own bucket a different way: `blizzard runner garden findings`, flagless — the
routine and the scope are derived server-side from the lease's own chunk, so a worker cannot point this read at another
routine's bucket, and it needs no hub credential in its child environment at all. It is a pure client of the runner's
local API, authorized by the spawn-injected lease identity, the same shape the `blizzard runner artifact` verbs take
(see [artifacts.md](./artifacts.md)); [openapi/runner.openapi.json](../../openapi/runner.openapi.json) owns the endpoint
shape.

A person takes a finding out of the live set for good with one of five exit verbs, each requiring `--note`:
`blizzard hub finding resolve <finding_id>...`, `confirm-gone <finding_id>...`, `wont-fix <finding_id>...`,
`not-a-finding <finding_id>...`, and `supersede <finding_id>... --by <finding_id>` (the absorbing finding). Each takes
one or many ids and exits them together, in one call. `blizzard hub finding reopen <finding_id>... --note <text>` undoes
whichever exit or `gone` fact was newest, restoring the finding to `live`. Every verb 404s on an unknown finding id and
422s on a blank note; a hand `resolve` from this verb records no garden proposal of its own.

**The hub board's Gardening tab reaches the same verbs from its own Findings sub-tab: a triage list over one
routine/scope bucket, further filterable by class and by state, beside the selected finding's own panel. Every one of
those four filters, and the selection itself, rides the URL, so a filtered bucket is a link and a row click never
discards a filter. `resolve`, `confirm-gone`, `wont-fix` and `not-a-finding` — or, on a finding that has already exited,
`reopen` — are dispatched one finding at a time from that panel, with a note. `supersede` is not among them: it needs
the absorbing finding's id, which the panel collects nowhere, so it stays a CLI and API verb. A `gone`-flagged finding
renders tinted but stays a normal, live, selectable row; an exited finding stays visible too, rendered dimmed rather
than removed. A finding named by an accepted-and-minted proposal shows that proposal's linked work item beside it.
Neither the triage actions nor their dialog is offered without `chunk:control`.**

## Garden proposals

`blizzard hub garden-proposal list` and `show <proposal_id>` read every proposal, or one by id, each naming the findings
it answers and the closure it carries once one exists. Neither takes a filter yet.

`blizzard hub garden-proposal pass <proposal_id> --reason <text>` records that the proposal was considered and declined,
with a reason required.
`blizzard hub garden-proposal accept <proposal_id> [--reason <text>]
[--body-file <path>|-] [--no-work-item]` records
agreement: by default it mints a linked hub work item carrying the proposal's own body, resting behind the ordinary
promote gate; `--body-file` supplies a different body (`-` for stdin); `--no-work-item` declines to mint, and the
decline is recorded rather than left to read as an absent link. Acceptance itself never promotes the minted item and
never changes a finding's state — but delivering the item it minted does: once that item closes, every finding the
proposal named that is still live is resolved, attributed to the proposal, the same as a hand
`blizzard hub finding resolve` but requiring no verb of its own. Re-delivering the same item resolves nothing a second
time. Either closing verb answers 409, naming the proposal's existing closure, when called again — closure is terminal.

**The hub board's Gardening tab renders a docket sheet on its own Proposals sub-tab, over the same reads: every
proposal, filtered by waiting state and by class, each read as prose beside its findings — read live, one finding at a
time, never a copy the proposal itself carries. Passing and accepting are each a dialog off the selected proposal, the
reason and the mint/decline choice exposed the same way the CLI takes them; an accepted proposal's linked work item,
once one exists, is read through the closure's own pointer and shown beside every finding it answers. Neither dialog is
offered without `chunk:control`.**

**Each evidence row also dispatches four of the exit verbs inline — `resolve`, `confirm-gone`, `wont-fix`,
`not-a-finding` — so the findings a proposal names can be triaged from the docket without leaving it. These are the one
place a note is not collected: the board writes one naming the proposal the row was triaged from, because the verbs
require a note server-side and a one-click action has nowhere to ask for one. `supersede` and `reopen` are not offered
here — the first needs an absorbing finding this row cannot name, and the second is not a way to clear a row off a
docket. The buttons are withheld on a row that has already exited, and withheld entirely without `chunk:control`; a
`gone`-flagged row still carries them, since it has not exited and confirming it is exactly what it is waiting for.**

## Trend

`blizzard hub routine trend <name>` reads a routine's finding inflow-against-outflow over a window, taking `--since`,
`--until`, and `--introduced-boundary` (each an instant, read in the operator's own local time, the same as
`blizzard hub analytics`'s own since/until flags) plus an optional `--period-days` (default 7). Per period it reports
findings created and exits per kind, plus the `outflow` (`resolved` and `gone-confirmed`) and `withdrawn` (the other
three exit kinds) roll-ups, and `reopened` — an exited finding's own undo, counted on its own rather than folded into
`created` or any exit count, so a resolve-reopen-resolve cycle inside one period reads as one creation, two exits, one
reopen, not an unexplained imbalance. Alongside the periods, `age` cuts the window's created findings against
`--introduced-boundary`: `recent` (at or after it), `older` (before it), and `unattributed` (no resolved `introduced`
instant at all — never guessed into either bucket). An unknown routine name answers 404; a malformed instant, a
`--period-days` under 1, `--until` not after `--since`, or a span/`--period-days` pair bucketing past 366 periods each
answer 422.
