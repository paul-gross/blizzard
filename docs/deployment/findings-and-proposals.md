# Findings and garden proposals

A **finding** is one observation a routine's run recorded; a **garden proposal** is a proposed response to one or more
findings. Both are hub-stored records. A finding carries no verb of its own yet; a garden proposal now carries the two
that close it.
[`domain/findings-and-proposals.md`](https://github.com/paul-gross/blizzard-context/blob/master/domain/findings-and-proposals.md)
owns the concept; this covers the verbs only.

## Findings

`blizzard hub finding list --routine <name> --scope <slug> [--include-gone]` and `show <finding_id>` are the finding
verbs. `list` is the read a running pass calls to cross-reference its own bucket — a routine's findings under one scope,
live only unless `--include-gone` names findings whose newest fact is `gone` too. `live`, `last_seen_at`, and
`observed_count` track each finding's own fact history, changing as later runs observe or lose it.

## Garden proposals

`blizzard hub garden-proposal list` and `show <proposal_id>` read every proposal, or one by id, each naming the
findings it answers and the closure it carries once one exists. Neither takes a filter yet.

`blizzard hub garden-proposal pass <proposal_id> --reason <text>` records that the proposal was considered and
declined, with a reason required. `blizzard hub garden-proposal accept <proposal_id> [--reason <text>]
[--body-file <path>|-] [--no-work-item]` records agreement: by default it mints a linked hub work item carrying the
proposal's own body, resting behind the ordinary promote gate; `--body-file` supplies a different body (`-` for
stdin); `--no-work-item` declines to mint, and the decline is recorded rather than left to read as an absent link.
Acceptance never promotes the minted item and never changes a finding's state. Either verb answers 409, naming the
proposal's existing closure, when called again — closure is terminal.
