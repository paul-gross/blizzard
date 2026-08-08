# Build — judgement

Assess the build against this node's criteria: the change implements the work item's intent, and your work is committed, pushed, and declared — `blizzard runner artifact commit` for each repo you touched. This node fuses build and verification; there is no separate verify node downstream to catch what slips through, so hold the bar here.

Judge the **work** as it now stands, not only what you did this turn: an increment an earlier attempt completed still counts, and you do not redo it.

The **declaration** is different, and does not carry over. Coverage is checked per attempt, and a re-attempt runs under a fresh lease — so a tip declared by an earlier attempt does not satisfy this one. If you did not run `blizzard runner artifact commit` on this attempt for every repo the chunk touches, do that before recording your verdict; re-declaring an unchanged tip is harmless, omitting it is not.

Select `pass` only if all of that holds — the work then hands to the review node for a cold-eyes pass. Select `fail` if the work does not yet meet the item's intent, or is not committed, pushed, and declared. The failure output is attached when the build node is re-entered.
