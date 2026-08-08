# Build — judgement

Assess the build against this node's criteria: the change implements the work item's intent, and your work is committed, pushed, and declared — `blizzard runner artifact commit` for each repo you touched. This node fuses build and verification; there is no separate verify node downstream to catch what slips through, so hold the bar here.

Judge the state as it now stands, not only the work you did this turn: work an earlier attempt completed still counts, and a tip an earlier attempt declared still counts.

Select `pass` only if all of that holds — the work then hands to the review node for a cold-eyes pass. Select `fail` if the work does not yet meet the item's intent, or is not committed, pushed, and declared. The failure output is attached when the build node is re-entered.
