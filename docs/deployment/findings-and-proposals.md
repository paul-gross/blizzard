# Findings and garden proposals

A **finding** is one observation a routine's run recorded; a **garden proposal** is a proposed response to one or more
findings. Both are hub-stored records, and only their read half exists so far — writing either is a sibling issue.
[`domain/findings-and-proposals.md`](https://github.com/paul-gross/blizzard-context/blob/master/domain/findings-and-proposals.md)
owns the concept; this covers the verbs only.

## Findings

`blizzard hub finding list --routine <name> --scope <slug> [--include-gone]` and `show <finding_id>` are the finding
verbs. `list` is the read a running pass calls to cross-reference its own bucket — a routine's findings under one scope,
live only unless `--include-gone` names findings whose newest fact is `gone` too. `live`, `last_seen_at`, and
`observed_count` track each finding's own fact history, changing as later runs observe or lose it.

## Garden proposals

`blizzard hub garden-proposal list` and `show <proposal_id>` are the proposal verbs — every proposal, or one by id, each
naming the findings it answers. Neither takes a filter yet.
