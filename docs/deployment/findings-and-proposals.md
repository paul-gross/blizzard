# Findings and garden proposals

A **finding** is one observation a routine's run recorded; a **garden proposal** is a proposed response to one or more
findings. Both are hub-stored records, and both now carry closing verbs of their own.
[`domain/findings-and-proposals.md`](https://github.com/paul-gross/blizzard-context/blob/master/domain/findings-and-proposals.md)
owns the concept; this covers the verbs only.

## Findings

`blizzard hub finding list --routine <name> --scope <slug> [--include-gone]` and `show <finding_id>` are the finding
reads. `list` is the read a running pass calls to cross-reference its own bucket — a routine's findings under one scope,
live only unless `--include-gone`, which now also surfaces every exited finding alongside a merely `gone` one. `state`,
`live`, `last_seen_at`, and `observed_count` track each finding's own fact history, changing as later runs observe or
lose it, or a person exits or reopens it.

A person takes a finding out of the live set for good with one of five exit verbs, each requiring `--note`:
`blizzard hub finding resolve <finding_id>...`, `confirm-gone <finding_id>...`, `wont-fix <finding_id>...`,
`not-a-finding <finding_id>...`, and `supersede <finding_id>... --by <finding_id>` (the absorbing finding). Each takes
one or many ids and exits them together, in one call. `blizzard hub finding reopen <finding_id>... --note <text>` undoes
whichever exit or `gone` fact was newest, restoring the finding to `live`. Every verb 404s on an unknown finding id and
422s on a blank note; a hand `resolve` from this verb records no garden proposal of its own.

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
